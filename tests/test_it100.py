import os
import socket
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import it100  # noqa: E402
from panel import (Panel, zone_node_name, P_ARMED_AWAY, P_ARMED_STAY, P_EXIT_DELAY,  # noqa: E402
                   P_ENTRY_DELAY, P_READY, P_NOT_READY, P_ALARM)


class ChecksumTests(unittest.TestCase):
    def test_known_frames(self):
        # Frames captured from the real panel / emulator
        self.assertEqual(it100.frame("001"), b"00191\r\n")
        self.assertEqual(it100.frame("030", "1"), b"0301C4\r\n")
        self.assertEqual(it100.frame("056", "1"), b"0561CC\r\n")

    def test_parse(self):
        self.assertEqual(it100.parse("65210" + it100.checksum("65210")), ("652", "10"))
        self.assertEqual(it100.parse("90014FE"), ("900", "14"))
        self.assertIsNone(it100.parse("90014FF"))   # bad checksum
        self.assertIsNone(it100.parse("12"))


class PanelTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.panel = Panel(lambda k, n: self.events.append((k, n)))
        self.p1 = self.panel.partitions[1]

    def feed(self, *msgs):
        for m in msgs:
            self.panel.handle(m[:3], m[3:])

    def test_arm_sequence(self):
        self.feed("6501")
        self.assertEqual(self.p1.state, P_READY)
        self.feed("6561")
        self.assertEqual(self.p1.state, P_EXIT_DELAY)
        self.feed("65210")
        self.assertEqual(self.p1.state, P_ARMED_AWAY)

    def test_ready_messages_do_not_hide_armed(self):
        # The panel can report ready/not-ready while armed; armed must win
        self.feed("65211", "6511", "6501")
        self.assertEqual(self.p1.state, P_ARMED_STAY)

    def test_entry_delay_alarm_disarm(self):
        self.feed("65210", "6571")
        self.assertEqual(self.p1.state, P_ENTRY_DELAY)
        self.feed("6541")
        self.assertEqual(self.p1.state, P_ALARM)
        self.assertIn(("alarm", 1), self.events)
        self.feed("6551", "6511")
        self.assertEqual(self.p1.state, P_NOT_READY)
        self.assertIn(("disarm", 1), self.events)

    def test_user_numbers(self):
        self.feed("70010003")
        self.assertEqual(self.p1.last_user, 3)

    def test_zones_and_labels(self):
        self.feed("609003")
        self.assertTrue(self.panel.zones[3].open)
        self.feed("610003")
        self.assertFalse(self.panel.zones[3].open)
        self.feed("570001Side Door                       ", "570033Zone    33                      ")
        self.assertEqual(self.panel.labels[1], "Side Door")
        self.assertTrue(self.panel.zone_has_custom_label(1))
        self.assertFalse(self.panel.zone_has_custom_label(33))

    def test_code_request(self):
        self.feed("90014")
        self.assertIn(("code", 1), self.events)

    def test_troubles(self):
        self.feed("802")
        self.assertTrue(self.panel.troubles["ac"])
        self.feed("803")
        self.assertFalse(self.panel.troubles["ac"])


class NamingTests(unittest.TestCase):
    # Raw keypad labels (two 16-character lines) and the names approved on the eISY
    APPROVED = {
        3: ("Foyer / Master  Bedroom Motion", "Z3 - Foyer-Master Bdrm Motion"),
        12: ("Living Room     North Window", "Z12 - Living Room North Win"),
        17: ("West Bedroom    South Window", "Z17 - West Bdrm South Window"),
        19: ("Master Bedroom  Northwest Wind", "Z19 - Master Bdrm NW Window"),
        20: ("West Bedroom    North Window", "Z20 - West Bdrm North Window"),
        22: ("Master Bedroom  East Window", "Z22 - Master Bdrm East Window"),
        23: ("Master Bedroom  North Window", "Z23 - Master Bdrm North Win"),
        1: ("Side Door", "Z1 - Side Door"),
    }

    def test_approved_names(self):
        panel = Panel()
        for zone, (raw, expected) in self.APPROVED.items():
            panel.handle("570", f"{zone:03d}{raw}")
            self.assertEqual(zone_node_name(zone, panel.labels[zone]), expected)
            self.assertLessEqual(len(expected), 29)


class FakeIT100:
    """Minimal TCP server that acts like ser2net + IT-100."""

    def __init__(self):
        self.srv = socket.socket()
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(1)
        self.port = self.srv.getsockname()[1]
        self.received = []
        self.conn = None
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        self.conn, _ = self.srv.accept()
        buf = b""
        while True:
            try:
                data = self.conn.recv(1024)
            except OSError:
                return
            if not data:
                return
            buf += data
            while b"\r\n" in buf:
                line, buf = buf.split(b"\r\n", 1)
                cmd, d = it100.parse(line.decode())
                self.received.append((cmd, d))
                self.conn.sendall(it100.frame("500", cmd))
                if cmd == "030":
                    self.conn.sendall(it100.frame("900", d + "4"))
                if cmd == "200":
                    self.conn.sendall(it100.frame("656", "1") + it100.frame("652", "10"))

    def close(self):
        if self.conn:
            self.conn.close()
        self.srv.close()


class LinkTests(unittest.TestCase):
    def test_arm_round_trip(self):
        fake = FakeIT100()
        panel = Panel()
        connected = threading.Event()
        link = it100.IT100("127.0.0.1", fake.port, on_message=panel.handle,
                           on_connect=connected.set)

        def on_change(kind, number):
            if kind == "code":
                link.send(it100.CMD_CODE_SEND, f"{number}1234")
        panel.listener = on_change

        link.start()
        self.assertTrue(connected.wait(5))
        link.send(it100.CMD_ARM_AWAY, "1")
        deadline = time.time() + 5
        while panel.partitions[1].state != P_ARMED_AWAY and time.time() < deadline:
            time.sleep(0.05)
        self.assertEqual(panel.partitions[1].state, P_ARMED_AWAY)
        self.assertIn(("200", "11234"), fake.received)
        link.stop()
        fake.close()

    def test_reconnects_after_drop(self):
        fake = FakeIT100()
        connects = []
        link = it100.IT100("127.0.0.1", fake.port, on_connect=lambda: connects.append(1))
        it100._RECONNECT_MIN = 0.2
        link.start()
        deadline = time.time() + 5
        while not connects and time.time() < deadline:
            time.sleep(0.05)
        fake.conn.close()          # drop the connection; server stops accepting
        fake.srv.close()
        fake2 = FakeIT100.__new__(FakeIT100)
        # restart a server on the same port
        fake2.srv = socket.socket()
        fake2.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        fake2.srv.bind(("127.0.0.1", fake.port))
        fake2.srv.listen(1)
        fake2.port, fake2.received, fake2.conn = fake.port, [], None
        threading.Thread(target=fake2._serve, daemon=True).start()
        deadline = time.time() + 10
        while len(connects) < 2 and time.time() < deadline:
            time.sleep(0.05)
        self.assertGreaterEqual(len(connects), 2)
        link.stop()
        fake2.close()


if __name__ == "__main__":
    unittest.main()
