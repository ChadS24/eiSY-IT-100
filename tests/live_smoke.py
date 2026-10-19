"""
Read-only check against the real panel through ser2net: reads labels and status, never arms.
Usage: python tests/live_smoke.py <host> [port]
"""
import logging
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import it100  # noqa: E402
from panel import Panel, zone_node_name  # noqa: E402

PSTATE = ["Not Ready", "Ready", "Ready (Force Arm)", "Exit Delay", "Entry Delay", "Armed Away",
          "Armed Stay", "Armed Away (NED)", "Armed Stay (NED)", "Alarm", "Busy"]

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
host, port = sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 10001

panel = Panel()
link = it100.IT100(host, port, on_message=panel.handle)
link.start()
for _ in range(50):
    if link.connected:
        break
    time.sleep(0.1)
if not link.connected:
    sys.exit("could not connect")

link.send(it100.CMD_DESCRIPTIVE_ARMING, "1")
link.send(it100.CMD_TIME_STAMP_CONTROL, "0")
link.send(it100.CMD_LABELS_REQUEST)
time.sleep(6)
link.send(it100.CMD_STATUS_REQUEST)
time.sleep(4)
link.stop()

zones = [z for z in range(1, 65) if panel.zone_has_custom_label(z)]
print(f"\nPanel version: {panel.version}")
print(f"Labels received: {len(panel.labels)}; zones with custom labels: {len(zones)}")
for z in zones:
    print(f"  zone{z:02d}  {'OPEN  ' if panel.zones[z].open else 'closed'}  {zone_node_name(z, panel.labels[z])}")
p1 = panel.partitions[1]
print(f"Partition 1: {PSTATE[p1.state]} (trouble={p1.trouble})")
print(f"Panel troubles: {panel.troubles}")
