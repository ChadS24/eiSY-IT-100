import udi_interface

LOGGER = udi_interface.LOGGER


class ZoneNode(udi_interface.Node):
    """One DSC zone. ST: 0 closed / 1 open. Sends DON when opened and DOF when closed."""

    id = "dsczone"
    drivers = [
        {"driver": "ST", "value": 0, "uom": 25},    # ZSTATE: closed/open
        {"driver": "GV0", "value": 0, "uom": 2},    # in alarm
        {"driver": "GV1", "value": 0, "uom": 2},    # tamper
        {"driver": "GV2", "value": 0, "uom": 2},    # fault
    ]

    def __init__(self, polyglot, primary, address, name, controller, zone_num):
        super().__init__(polyglot, primary, address, name)
        self.controller = controller
        self.zone_num = zone_num

    def update(self, zone, report_cmd=True):
        was_open = self.getDriver("ST")
        self.setDriver("ST", 1 if zone.open else 0)
        self.setDriver("GV0", int(zone.alarm))
        self.setDriver("GV1", int(zone.tamper))
        self.setDriver("GV2", int(zone.fault))
        if report_cmd and was_open is not None and int(was_open) != int(zone.open):
            self.reportCmd("DON" if zone.open else "DOF")

    def query(self, command=None):
        self.controller.request_status()
        self.reportDrivers()

    commands = {"QUERY": query}
