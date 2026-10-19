import datetime
import re
import threading

import udi_interface

import it100
from panel import Panel, zone_node_name
from nodes.partition import PartitionNode
from nodes.zone import ZoneNode

LOGGER = udi_interface.LOGGER
Custom = udi_interface.Custom

DEFAULTS = {
    "host": "",
    "port": "10001",
    "usercode": "",
    "partitions": "1",
    "zones": "auto",
}
FALLBACK_ZONES = 8          # "auto" with no custom labels (e.g. LED keypads only)
LABEL_SETTLE_SECONDS = 3    # wait this long after the last label before creating zone nodes


def parse_zone_list(text):
    """'8' -> 1..8, '1-32,40' -> [1..32, 40]."""
    text = text.strip()
    if text.isdigit():
        return list(range(1, min(int(text), 64) + 1))
    zones = set()
    for part in text.split(","):
        part = part.strip()
        m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", part)
        if m:
            zones.update(range(int(m.group(1)), int(m.group(2)) + 1))
        elif part.isdigit():
            zones.add(int(part))
    return sorted(z for z in zones if 1 <= z <= 64)


class Controller(udi_interface.Node):
    """DSC panel via IT-100. ST is the plugin's online status (managed by PG3)."""

    id = "dscpanel"
    drivers = [
        {"driver": "ST", "value": 0, "uom": 2},     # plugin online
        {"driver": "GV0", "value": 0, "uom": 2},    # IT-100 connected
        {"driver": "GV1", "value": 0, "uom": 2},    # AC power trouble
        {"driver": "GV2", "value": 0, "uom": 2},    # panel battery trouble
        {"driver": "GV3", "value": 0, "uom": 2},    # bell trouble
        {"driver": "GV4", "value": 0, "uom": 2},    # system tamper
        {"driver": "GV5", "value": 0, "uom": 2},    # keybus fault
    ]

    def __init__(self, polyglot, primary, address, name):
        super().__init__(polyglot, primary, address, name)
        self.poly = polyglot
        self.Parameters = Custom(polyglot, "customparams")
        self.Notices = Custom(polyglot, "notices")

        self.panel = Panel(self._on_panel_change)
        self.link = None
        self.user_code = ""
        self._params_ready = threading.Event()
        self._node_lock = threading.RLock()
        self._label_timer = None
        self._labels_done = False
        self._started = False

        polyglot.subscribe(polyglot.START, self.start, address)
        polyglot.subscribe(polyglot.CUSTOMPARAMS, self.parameter_handler)
        polyglot.subscribe(polyglot.POLL, self.poll)
        polyglot.subscribe(polyglot.STOP, self.stop)
        polyglot.ready()
        polyglot.addNode(self, conn_status="ST")

    # ------------------------------------------------------------------ PG3 events

    def parameter_handler(self, params):
        self.Parameters.load(params)
        missing = {k: v for k, v in DEFAULTS.items() if self.Parameters[k] is None}
        if missing:
            for k, v in missing.items():
                self.Parameters[k] = v   # triggers another CUSTOMPARAMS event with the full set
            return

        self.user_code = str(self.Parameters["usercode"] or "").strip()
        self.Notices.clear()
        if not self.user_code.isdigit() or len(self.user_code) not in (4, 6):
            self.Notices["usercode"] = "Set 'usercode' to your 4- or 6-digit alarm code (needed to arm and disarm)."

        host, port = str(self.Parameters["host"]).strip(), str(self.Parameters["port"]).strip()
        if not host:
            self.Notices["host"] = "Set 'host' to the IP address of the ser2net bridge (e.g. the Raspberry Pi)."
        if self.link and (self.link.host != host or str(self.link.port) != port):
            LOGGER.info("Connection settings changed; reconnecting")
            self.link.stop()
            self.link = None
        if self._started and self.link is None:
            self._connect()
        self._params_ready.set()

    def start(self):
        self.poly.updateProfile()
        self.poly.setCustomParamsDoc()
        if not self._params_ready.wait(30):
            LOGGER.error("No configuration received from PG3; using defaults")
        self._started = True
        self._create_partition_nodes()
        self._connect()

    def poll(self, flag):
        if "shortPoll" in flag and self.link and self.link.connected:
            self.link.send(it100.CMD_POLL)   # keepalive; a failed send triggers a reconnect

    def stop(self):
        LOGGER.info("Stopping; disconnecting from IT-100")
        if self.link:
            self.link.stop()
        self.setDriver("GV0", 0)

    # ------------------------------------------------------------------ link

    def _connect(self):
        host = str(self.Parameters["host"] or "").strip()
        if not host:
            LOGGER.error("No host configured; not connecting")
            return
        self.link = it100.IT100(
            host, int(self.Parameters["port"]),
            on_message=self.panel.handle, on_connect=self._on_connect,
            on_disconnect=self._on_disconnect, logger=LOGGER)
        self.link.start()

    def _on_connect(self):
        self.setDriver("GV0", 1)
        self.send(it100.CMD_DESCRIPTIVE_ARMING, "1")   # 652 includes the arming mode
        self.send(it100.CMD_TIME_STAMP_CONTROL, "0")
        self.send(it100.CMD_LABELS_REQUEST)            # zone names; nodes created once labels settle
        if not self._labels_done:
            self._restart_label_timer(15)              # fallback if the panel sends no labels
        self.request_status()

    def _on_disconnect(self):
        self.setDriver("GV0", 0)

    def send(self, cmd, data=""):
        return bool(self.link) and self.link.send(cmd, data)

    def send_keys(self, keys, partition=1):
        if self.link and self.link.connected:
            threading.Thread(target=self.link.send_keys, args=(keys, partition), daemon=True).start()

    def request_status(self):
        self.send(it100.CMD_STATUS_REQUEST)

    def notice_missing_code(self):
        self.Notices["usercode"] = "Cannot arm/disarm: set 'usercode' in the plugin configuration."
        LOGGER.error("No valid usercode configured")

    # ------------------------------------------------------------------ panel events

    def _on_panel_change(self, kind, number):
        if kind == "partition":
            node = self.poly.getNode(f"part{number}")
            if node:
                node.update(self.panel.partitions[number])
        elif kind == "zone":
            node = self.poly.getNode(f"zone{number:02d}")
            if node is None and self._zone_wanted(number):
                node = self._add_zone_node(number)
            if node:
                node.update(self.panel.zones[number])
        elif kind == "panel":
            t = self.panel.troubles
            self.setDriver("GV1", int(t["ac"]))
            self.setDriver("GV2", int(t["battery"]))
            self.setDriver("GV3", int(t["bell"]))
            self.setDriver("GV4", int(t["tamper"]))
            self.setDriver("GV5", int(t["keybus"]))
        elif kind == "label" and 1 <= number <= 64:
            self._restart_label_timer()
        elif kind == "code":
            self._send_code(number)
        elif kind in ("alarm", "disarm"):
            node = self.poly.getNode(f"part{number}")
            if node:
                node.reportCmd("DON" if kind == "alarm" else "DOF")

    def _send_code(self, partition):
        if not self.user_code:
            self.notice_missing_code()
            return
        LOGGER.info("Panel requested a code for partition %d; sending user code", partition)
        self.send(it100.CMD_CODE_SEND, f"{partition}{self.user_code}")

    # ------------------------------------------------------------------ nodes

    def _create_partition_nodes(self):
        try:
            count = max(1, min(8, int(self.Parameters["partitions"])))
        except (TypeError, ValueError):
            count = 1
        with self._node_lock:
            for n in range(1, count + 1):
                if self.poly.getNode(f"part{n}") is None:
                    self.poly.addNode(PartitionNode(self.poly, self.address, f"part{n}", f"Partition {n}", self, n))

    def _zone_setting(self):
        return str(self.Parameters["zones"] or "auto").strip().lower()

    def _zone_wanted(self, zone):
        setting = self._zone_setting()
        if setting == "auto":
            if not self._labels_done:
                return False   # discover() creates the nodes once labels have arrived
            return self.panel.zone_has_custom_label(zone) or not any(
                self.panel.zone_has_custom_label(z) for z in range(1, 65))
        return zone in parse_zone_list(setting)

    def _restart_label_timer(self, seconds=LABEL_SETTLE_SECONDS):
        if self._label_timer:
            self._label_timer.cancel()
        self._label_timer = threading.Timer(seconds, self.discover)
        self._label_timer.daemon = True
        self._label_timer.start()

    def _add_zone_node(self, zone):
        with self._node_lock:
            address = f"zone{zone:02d}"
            node = self.poly.getNode(address)
            if node is None:
                label = self.panel.labels.get(zone, "")
                name = zone_node_name(zone, label) if self.panel.zone_has_custom_label(zone) else f"Zone {zone}"
                LOGGER.info("Adding zone node %s: %s", address, name)
                node = self.poly.addNode(ZoneNode(self.poly, self.address, address, name, self, zone))
            return node

    def discover(self, command=None):
        """Create zone nodes per the 'zones' setting (labels must have been read for 'auto')."""
        setting = self._zone_setting()
        if setting == "auto":
            zones = [z for z in range(1, 65) if self.panel.zone_has_custom_label(z)]
            if not zones:
                zones = list(range(1, FALLBACK_ZONES + 1))
        else:
            zones = parse_zone_list(setting)
        for z in zones:
            node = self._add_zone_node(z)
            if node:
                node.update(self.panel.zones[z], report_cmd=False)
        self._labels_done = True
        LOGGER.info("Zone nodes: %s", zones)

    # ------------------------------------------------------------------ commands

    def cmd_discover(self, command=None):
        # Re-read labels; nodes for newly labelled zones are created when they arrive
        self.send(it100.CMD_LABELS_REQUEST)

    def cmd_set_time(self, command=None):
        now = datetime.datetime.now()
        self.send(it100.CMD_SET_TIME_DATE, now.strftime("%H%M%m%d%y"))

    def query(self, command=None):
        self.request_status()
        for node in self.poly.getNodes().values():
            node.reportDrivers()

    commands = {
        "QUERY": query,
        "DISCOVER": cmd_discover,
        "SET_TIME": cmd_set_time,
    }
