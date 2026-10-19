"""End-to-end test of the plugin's nodes against a fake IT-100, with udi_interface stubbed out."""
import os
import socket
import sys
import threading
import time
import unittest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, HERE)

import fake_udi_interface  # noqa: E402
sys.modules["udi_interface"] = fake_udi_interface

import it100  # noqa: E402
import nodes.controller as controller_mod  # noqa: E402
from nodes import Controller  # noqa: E402
from panel import P_ARMED_AWAY, P_READY  # noqa: E402

LABELS = {1: "Side Door", 2: "Family Rm Door", 3: "Foyer / Master  Bedroom Motion", 33: "Zone    33"}


class FakePanel:
    """Answers like an IT-100: labels, a status dump, and the arm/code exchange."""

    def __init__(self):
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(1)
        self.port = self.srv.getsockname()[1]
        self.received = []
        threading.Thread(target=self._serve, daemon=True).start()

    def send(self, cmd, data=""):
        self.conn.sendall(it100.frame(cmd, data))

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
                self.send("500", cmd)
                if cmd == "002":
                    for n, text in LABELS.items():
                        self.send("570", f"{n:03d}{text:<32}")
                elif cmd == "001":
                    self.send("6501")
                    self.send("609002")
                    self.send("610001")
                elif cmd == "030":
                    self.send("900", d + "4")
                elif cmd == "200":
                    self.send("6561")
                    self.send("65210")
                elif cmd == "040":
                    self.send("6551")
                    self.send("6501")

    def close(self):
        self.conn.close()
        self.srv.close()


def wait_for(cond, timeout=8):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


class ControllerTests(unittest.TestCase):
    def test_full_flow(self):
        controller_mod.LABEL_SETTLE_SECONDS = 0.3
        fake = FakePanel()
        poly = fake_udi_interface.Interface()
        ctl = Controller(poly, "controller", "controller", "DSC Panel")
        params = {"host": "127.0.0.1", "port": str(fake.port), "usercode": "1234",
                  "partitions": "1", "zones": "auto"}
        for cb in poly.handlers["custom_params"]:
            cb(params)
        threading.Thread(target=ctl.start, daemon=True).start()

        # Zone nodes from custom labels only, with eISY-safe names
        self.assertTrue(wait_for(lambda: poly.getNode("zone03") is not None))
        self.assertEqual(poly.getNode("zone01").name, "Z1 - Side Door")
        self.assertEqual(poly.getNode("zone03").name, "Z3 - Foyer-Master Bdrm Motion")
        self.assertIsNone(poly.getNode("zone33"))
        self.assertEqual(ctl.getDriver("GV0"), 1)

        part = poly.getNode("part1")
        self.assertTrue(wait_for(lambda: part.getDriver("ST") == P_READY))
        self.assertTrue(wait_for(lambda: poly.getNode("zone02").getDriver("ST") == 1))

        # Arm away: panel asks for the code, plugin answers, partition shows Armed Away
        part.arm_away()
        self.assertTrue(wait_for(lambda: part.getDriver("ST") == P_ARMED_AWAY))
        self.assertIn(("200", "11234"), fake.received)

        # Disarm sends the padded 6-digit code and reports DOF
        part.disarm()
        self.assertTrue(wait_for(lambda: part.getDriver("ST") == P_READY))
        self.assertIn(("040", "1123400"), fake.received)
        self.assertIn("DOF", part.reported)

        # Zone open/close reports DON/DOF
        fake.send("609001")
        z1 = poly.getNode("zone01")
        self.assertTrue(wait_for(lambda: z1.getDriver("ST") == 1))
        self.assertIn("DON", z1.reported)

        ctl.stop()
        fake.close()


if __name__ == "__main__":
    unittest.main()
