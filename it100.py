"""
DSC IT-100 serial protocol client over TCP (e.g. ser2net on a Raspberry Pi).

No Polyglot dependencies, so it can be tested on its own.

Frame format (both directions): 3-digit command + data + 2 hex checksum + CR LF.
The checksum is the low byte of the sum of the ASCII codes of command + data.
"""
import logging
import socket
import threading
import time

_LOGGER = logging.getLogger(__name__)

# Commands sent to the IT-100
CMD_POLL = "000"
CMD_STATUS_REQUEST = "001"
CMD_LABELS_REQUEST = "002"
CMD_SET_TIME_DATE = "010"
CMD_ARM_AWAY = "030"
CMD_ARM_STAY = "031"
CMD_ARM_NO_ENTRY_DELAY = "032"
CMD_DISARM = "040"
CMD_DESCRIPTIVE_ARMING = "050"
CMD_TIME_STAMP_CONTROL = "055"
CMD_TIME_BCAST_CONTROL = "056"
CMD_VIRTUAL_KEYPAD_CONTROL = "058"
CMD_KEY_PRESSED = "070"
CMD_CODE_SEND = "200"

# Notifications received from the IT-100
NOTIFY_ACK = "500"
NOTIFY_ERROR = "501"
NOTIFY_SYSTEM_ERROR = "502"
NOTIFY_LABELS = "570"
NOTIFY_CODE_REQUIRED = "900"

_RECONNECT_MIN = 5
_RECONNECT_MAX = 60
_SOCKET_TIMEOUT = 300   # no data at all for 5 minutes -> assume the link is dead


def checksum(cmd_data: str) -> str:
    return "{:02X}".format(sum(ord(c) for c in cmd_data) & 0xFF)


def frame(cmd: str, data: str = "") -> bytes:
    msg = cmd + data
    return (msg + checksum(msg) + "\r\n").encode("ascii")


def parse(line: str):
    """Return (cmd, data) for a valid line, or None if malformed or the checksum is wrong."""
    line = line.strip()
    if len(line) < 5:
        return None
    body, ck = line[:-2], line[-2:]
    if checksum(body) != ck.upper():
        return None
    return body[:3], body[3:]


class IT100:
    """
    Maintains a TCP connection to the IT-100 and dispatches received messages.

    Callbacks (all optional) run on the reader thread:
        on_message(cmd, data)
        on_connect()
        on_disconnect()
    """

    def __init__(self, host, port, on_message=None, on_connect=None, on_disconnect=None, logger=_LOGGER):
        self.host = host
        self.port = int(port)
        self.on_message = on_message
        self.on_connect = on_connect
        self.on_disconnect = on_disconnect
        self._logger = logger
        self._sock = None
        self._send_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None

    @property
    def connected(self) -> bool:
        return self._sock is not None

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="it100-reader", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._close()

    def send(self, cmd: str, data: str = "") -> bool:
        """Send a command; returns False (and logs) if not connected or the send failed."""
        sock = self._sock
        if sock is None:
            self._logger.warning("IT-100 not connected; dropped command %s", cmd)
            return False
        try:
            with self._send_lock:
                sock.sendall(frame(cmd, data))
            self._logger.debug("IT-100 out: %s%s", cmd, _mask(cmd, data))
            return True
        except OSError as err:
            self._logger.error("IT-100 send failed (%s); reconnecting", err)
            self._close()
            return False

    def send_keys(self, keys: str, partition: int = 1):
        """
        Press keypad keys via the IT-100 virtual keypad (applies to partition 1 on most panels).
        Each key is followed by a key-release ('^'), as the IT-100 expects.
        """
        self.send(CMD_VIRTUAL_KEYPAD_CONTROL, "1")
        time.sleep(0.5)
        for key in keys:
            self.send(CMD_KEY_PRESSED, key)
            time.sleep(0.25)
            self.send(CMD_KEY_PRESSED, "^")
            time.sleep(0.25)
        self.send(CMD_VIRTUAL_KEYPAD_CONTROL, "0")

    # ------------------------------------------------------------------ internals

    def _close(self):
        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
            if self.on_disconnect:
                self.on_disconnect()

    def _run(self):
        delay = _RECONNECT_MIN
        while not self._stop.is_set():
            try:
                self._logger.info("Connecting to IT-100 at %s:%d", self.host, self.port)
                sock = socket.create_connection((self.host, self.port), timeout=10)
                sock.settimeout(_SOCKET_TIMEOUT)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            except OSError as err:
                self._logger.error("IT-100 connection failed: %s (retry in %ds)", err, delay)
                self._stop.wait(delay)
                delay = min(delay * 2, _RECONNECT_MAX)
                continue

            self._sock = sock
            delay = _RECONNECT_MIN
            self._logger.info("Connected to IT-100")
            if self.on_connect:
                try:
                    self.on_connect()
                except Exception:
                    self._logger.exception("Error in on_connect handler")
            self._read_loop(sock)
            self._close()

    def _read_loop(self, sock):
        buf = b""
        while not self._stop.is_set() and self._sock is sock:
            try:
                chunk = sock.recv(1024)
            except socket.timeout:
                self._logger.error("No data from IT-100 for %ds; reconnecting", _SOCKET_TIMEOUT)
                return
            except OSError as err:
                if self._sock is sock:
                    self._logger.error("IT-100 connection lost: %s", err)
                return
            if not chunk:
                self._logger.error("IT-100 connection closed by remote end")
                return
            buf += chunk
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                line = raw.decode("ascii", "replace").strip()
                if not line:
                    continue
                msg = parse(line)
                if msg is None:
                    self._logger.warning("IT-100 bad message ignored: %r", line)
                    continue
                self._logger.debug("IT-100 in:  %s%s", msg[0], msg[1])
                if self.on_message:
                    try:
                        self.on_message(*msg)
                    except Exception:
                        self._logger.exception("Error handling IT-100 message %s%s", *msg)


def _mask(cmd, data):
    """Keep user codes out of the log."""
    if cmd in (CMD_CODE_SEND, CMD_DISARM):
        return data[:1] + "*" * (len(data) - 1) if cmd == CMD_DISARM else "*" * len(data)
    return data
