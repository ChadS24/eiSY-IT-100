import udi_interface

import it100

LOGGER = udi_interface.LOGGER


class PartitionNode(udi_interface.Node):
    """
    One DSC partition. ST uses the PSTATE index (see panel.py).
    Sends DON when the partition goes into alarm and DOF when it is disarmed.
    """

    id = "dscpart"
    drivers = [
        {"driver": "ST", "value": 0, "uom": 25},    # PSTATE
        {"driver": "GV0", "value": 0, "uom": 70},   # last arm/disarm user number
        {"driver": "GV1", "value": 0, "uom": 2},    # partition trouble
    ]

    def __init__(self, polyglot, primary, address, name, controller, part_num):
        super().__init__(polyglot, primary, address, name)
        self.controller = controller
        self.part_num = part_num

    def update(self, part):
        self.setDriver("ST", part.state)
        self.setDriver("GV0", part.last_user)
        self.setDriver("GV1", int(part.trouble))

    # ------------------------------------------------------------------ commands

    def _arm(self, cmd, label):
        LOGGER.info("Partition %d: %s", self.part_num, label)
        self.controller.send(cmd, str(self.part_num))
        # The panel answers with 900 (code required) if it needs a code; the controller replies.

    def arm_away(self, command=None):
        self._arm(it100.CMD_ARM_AWAY, "arm away")

    def arm_stay(self, command=None):
        self._arm(it100.CMD_ARM_STAY, "arm stay")

    def arm_no_entry_delay(self, command=None):
        self._arm(it100.CMD_ARM_NO_ENTRY_DELAY, "arm with no entry delay")

    def disarm(self, command=None):
        LOGGER.info("Partition %d: disarm", self.part_num)
        code = self.controller.user_code
        if not code:
            self.controller.notice_missing_code()
            return
        if len(code) == 4:
            code += "00"   # the IT-100 expects 6 digits; 4-digit codes are padded with 00
        self.controller.send(it100.CMD_DISARM, f"{self.part_num}{code}")

    def toggle_chime(self, command=None):
        LOGGER.info("Partition %d: toggle door chime", self.part_num)
        self.controller.send_keys("*4", self.part_num)

    def query(self, command=None):
        self.controller.request_status()
        self.reportDrivers()

    commands = {
        "DISARM": disarm,
        "ARM_AWAY": arm_away,
        "ARM_STAY": arm_stay,
        "ARM_NED": arm_no_entry_delay,
        "CHIME": toggle_chime,
        "QUERY": query,
    }
