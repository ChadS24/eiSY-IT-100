"""
DSC panel state built from IT-100 notifications. No Polyglot dependencies.

The model keeps state for the panel, each partition and each zone, and calls
`listener(kind, number)` whenever something changes:
    kind = "panel"     (number is None)
    kind = "partition" (number = partition 1-8)
    kind = "zone"      (number = zone 1-64)
    kind = "label"     (number = label number; zone labels are 1-64)
    kind = "alarm" / "disarm" / "code" (number = partition) for one-shot events
"""
import re

# Partition states (index values shared with the profile's PSTATE editor)
P_NOT_READY = 0
P_READY = 1
P_READY_FORCE = 2
P_EXIT_DELAY = 3
P_ENTRY_DELAY = 4
P_ARMED_AWAY = 5
P_ARMED_STAY = 6
P_ARMED_AWAY_NED = 7
P_ARMED_STAY_NED = 8
P_ALARM = 9
P_BUSY = 10

_ARMED_MODE = {"0": P_ARMED_AWAY, "1": P_ARMED_STAY, "2": P_ARMED_AWAY_NED, "3": P_ARMED_STAY_NED}

_DEFAULT_ZONE_LABEL = re.compile(r"^Zone\s*\d+$", re.I)


class Partition:
    def __init__(self):
        self.ready = False        # last 650/651/653 (tracked separately from armed)
        self.force_ready = False
        self.armed_state = None   # one of the P_ARMED_* values, or None when disarmed
        self.delay = None         # P_EXIT_DELAY / P_ENTRY_DELAY while a delay is running
        self.alarm = False
        self.busy = False
        self.trouble = False
        self.last_user = 0

    @property
    def state(self) -> int:
        if self.alarm:
            return P_ALARM
        if self.delay == P_ENTRY_DELAY:
            return P_ENTRY_DELAY
        if self.armed_state is not None:
            return self.armed_state
        if self.delay == P_EXIT_DELAY:
            return P_EXIT_DELAY
        if self.busy:
            return P_BUSY
        if self.ready:
            return P_READY_FORCE if self.force_ready else P_READY
        return P_NOT_READY


class Zone:
    def __init__(self):
        self.open = False
        self.alarm = False
        self.tamper = False
        self.fault = False


class Panel:
    def __init__(self, listener=None):
        self.listener = listener or (lambda kind, number: None)
        self.partitions = {n: Partition() for n in range(1, 9)}
        self.zones = {n: Zone() for n in range(1, 65)}
        self.labels = {}
        self.troubles = {"ac": False, "battery": False, "bell": False, "tamper": False, "keybus": False}
        self.version = ""

    # ------------------------------------------------------------------ helpers

    def zone_has_custom_label(self, zone: int) -> bool:
        label = self.labels.get(zone, "")
        return bool(label) and not _DEFAULT_ZONE_LABEL.match(label)

    def _p(self, data):
        return int(data[:1]), self.partitions[int(data[:1])]

    def _z(self, data):
        n = int(data[-3:])
        return n, self.zones.get(n)

    def _notify(self, kind, number=None):
        self.listener(kind, number)

    # ------------------------------------------------------------------ dispatch

    def handle(self, cmd: str, data: str):
        handler = getattr(self, "_h" + cmd, None)
        if handler:
            handler(data)

    # Labels
    def _h570(self, data):
        n = int(data[:3])
        self.labels[n] = re.sub(r"\s+", " ", data[3:]).strip()
        self._notify("label", n)

    def _h908(self, data):
        self.version = data

    # Zones
    def _zone_flag(self, data, attr, value):
        n, z = self._z(data)
        if z is not None and getattr(z, attr) != value:
            setattr(z, attr, value)
            self._notify("zone", n)

    def _h609(self, d): self._zone_flag(d, "open", True)
    def _h610(self, d): self._zone_flag(d, "open", False)
    def _h601(self, d): self._zone_flag(d, "alarm", True)
    def _h602(self, d): self._zone_flag(d, "alarm", False)
    def _h603(self, d): self._zone_flag(d, "tamper", True)
    def _h604(self, d): self._zone_flag(d, "tamper", False)
    def _h605(self, d): self._zone_flag(d, "fault", True)
    def _h606(self, d): self._zone_flag(d, "fault", False)

    # Partitions
    def _update_partition(self, data, **changes):
        n, p = self._p(data)
        for k, v in changes.items():
            setattr(p, k, v)
        self._notify("partition", n)
        return n, p

    def _h650(self, d): self._update_partition(d, ready=True, force_ready=False, busy=False)
    def _h651(self, d): self._update_partition(d, ready=False, force_ready=False, busy=False)
    def _h653(self, d): self._update_partition(d, ready=True, force_ready=True, busy=False)
    def _h673(self, d): self._update_partition(d, busy=True)

    def _h652(self, d):
        # Descriptive arming gives partition + mode; without it there is no mode (treat as away)
        mode = d[1:2] or "0"
        self._update_partition(d, armed_state=_ARMED_MODE.get(mode, P_ARMED_AWAY), delay=None, busy=False)

    def _h654(self, d):
        n, _ = self._update_partition(d, alarm=True, delay=None)
        self._notify("alarm", n)

    def _h655(self, d):
        n, _ = self._update_partition(d, armed_state=None, alarm=False, delay=None, busy=False)
        self._notify("disarm", n)

    def _h656(self, d): self._update_partition(d, delay=P_EXIT_DELAY, busy=False)
    def _h657(self, d): self._update_partition(d, delay=P_ENTRY_DELAY)

    def _h700(self, d): self._update_partition(d, last_user=int(d[1:] or 0))   # user closing
    def _h750(self, d): self._update_partition(d, last_user=int(d[1:] or 0))   # user opening
    def _h701(self, d): self._update_partition(d, last_user=0)                 # special closing
    def _h751(self, d): self._update_partition(d, last_user=0)                 # special opening

    def _h840(self, d): self._update_partition(d, trouble=True)
    def _h841(self, d): self._update_partition(d, trouble=False)

    def _h900(self, d):
        # Code required: data is partition + code length (e.g. "14")
        self._notify("code", int(d[:1]) if d else 1)

    # Panel troubles
    def _trouble(self, key, value):
        if self.troubles[key] != value:
            self.troubles[key] = value
            self._notify("panel")

    def _h800(self, d): self._trouble("battery", True)
    def _h801(self, d): self._trouble("battery", False)
    def _h802(self, d): self._trouble("ac", True)
    def _h803(self, d): self._trouble("ac", False)
    def _h806(self, d): self._trouble("bell", True)
    def _h807(self, d): self._trouble("bell", False)
    def _h829(self, d): self._trouble("tamper", True)
    def _h830(self, d): self._trouble("tamper", False)
    def _h896(self, d): self._trouble("keybus", True)
    def _h897(self, d): self._trouble("keybus", False)


# ---------------------------------------------------------------------- naming

MAX_NAME = 29   # eISY truncates node names beyond this

# Applied in order, only while the name is still too long
_ABBREVIATIONS = [
    (r"\bBedroom\b", "Bdrm"),
    (r"\bNorthwest\b", "NW"), (r"\bNortheast\b", "NE"),
    (r"\bSouthwest\b", "SW"), (r"\bSoutheast\b", "SE"),
    (r"\bWindows\b", "Wins"), (r"\bWindow\b", "Win"),
    (r"\bLiving Room\b", "LR"), (r"\bFamily Room\b", "FR"),
    (r"\bMaster\b", "Mstr"),
]


def zone_node_name(zone: int, label: str) -> str:
    """
    "Z<n> - <label>", fitted to the eISY's 29-character name limit.
    '/' is not allowed in eISY names; labels cut off by the keypad's 16-character
    lines (e.g. "Wind") are completed first.
    """
    text = re.sub(r"\s*/\s*", "-", label)
    text = re.sub(r"\bWind$", "Window", text)
    name = f"Z{zone} - {text}"
    for pattern, short in _ABBREVIATIONS:
        if len(name) <= MAX_NAME:
            break
        name = re.sub(pattern, short, name)
    return name[:MAX_NAME].rstrip()
