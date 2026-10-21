# IT100-Direct: DSC IT-100 plugin for eISY (PG3x)

Talks to a DSC alarm panel through an IT-100 module directly, using the IT-100 protocol.
The IT-100 stays on the Raspberry Pi's USB-serial port; `ser2net` on the Pi exposes it on TCP
port 10001, and this plugin (running on the eISY) connects to that port.

```
DSC panel ── IT-100 ── USB-serial ── Raspberry Pi (ser2net :10001) ── LAN ── eISY (this plugin)
```

This replaces the Envisalink emulator (`evl-emu`) and the EnvisaLink-DSC plugin.

## What you get

| Node | Address | Shows | Buttons |
|---|---|---|---|
| DSC Panel | `controller` | IT-100 connected, AC power, panel battery, bell, tamper, keybus | Query, Re-read Zone Labels, Set Panel Clock |
| Partition N | `partN` | State (Ready, Not Ready, Exit/Entry Delay, Armed Away/Stay/No Entry Delay, Alarm, Busy), last user, trouble | Disarm, Arm Away, Arm Stay, Arm No Entry Delay, Toggle Door Chime |
| Zone N | `zoneNN` | Closed/Open, in alarm, tamper, fault | Query |

- Zone nodes are created only for zones with a custom keypad label (setting `zones` = `auto`) and are
  named `Z<n> - <label>`, shortened to fit the eISY's 29-character limit (`Bedroom`→`Bdrm`,
  `Window`→`Win` and so on, only when needed). Names you change in the Admin Console are kept.
- Programs: zones send **DON** when opened and **DOF** when closed; partitions send **DON** on alarm
  and **DOF** when disarmed.
- When the panel asks for a code while arming, the plugin answers with `usercode`. Codes are never
  written to the log.

## Settings

See [POLYGLOT_CONFIG.md](POLYGLOT_CONFIG.md). Set `usercode` before arming or disarming.

## Files

| Path | Purpose |
|---|---|
| `dsc-it100.py` | Plugin entry point |
| `it100.py` | IT-100 protocol over TCP: framing, checksums, reconnects |
| `panel.py` | Panel/partition/zone state from IT-100 messages; node naming |
| `nodes/` | PG3 nodes (controller, partition, zone) |
| `profile/` | eISY node definitions, editors and text |
| `pi/ser2net.yaml` | ser2net config for the Pi |
| `tests/` | Unit tests (no eISY needed) and `live_smoke.py`, a read-only check against the real panel |

Run the tests: `python -m unittest discover -s tests -v`

## Raspberry Pi setup (ser2net)

1. Give the IT-100's USB-serial adapter a stable name with a udev rule, e.g.
   `/etc/udev/rules.d/99-it100.rules` (use your adapter's vendor ID and serial number from
   `udevadm info /dev/ttyUSB0`):
   ```
   SUBSYSTEM=="tty", ATTRS{idVendor}=="0403", ATTRS{serial}=="<adapter-serial>", SYMLINK+="it100", GROUP="dialout", MODE="0660"
   ```
2. Install ser2net and the config from this repo:
   ```bash
   sudo apt-get install -y ser2net && sudo install -m 644 pi/ser2net.yaml /etc/ser2net.yaml && sudo systemctl enable --now ser2net
   ```

## Cutover

The serial port can only be used by one program, so evl-emu and ser2net cannot run at the same
time. Until step 1, the old setup keeps working unchanged.

1. **Pi: switch from evl-emu to ser2net** (after the Pi setup above, with ser2net installed but disabled):
   ```bash
   ssh <pi-user>@<pi-ip> "sudo systemctl disable --now evl-emu && sudo systemctl enable --now ser2net"
   ```
   The old EnvisaLink-DSC plugin goes offline at this point.
2. **eISY: copy the plugin** to the eISY:
   ```bash
   ssh <eisy-user>@<eisy-ip> "mkdir -p dsc-it100"
   ```
   ```bash
   scp -r dsc-it100.py it100.py panel.py nodes profile server.json requirements.txt install.sh POLYGLOT_CONFIG.md README.md <eisy-user>@<eisy-ip>:dsc-it100/
   ```
3. **PG3x: install it as a local plugin** from `/home/<eisy-user>/dsc-it100` (Developer → Add new plugin: name `IT100-Direct`, store Local, Dev Mode, plugin user `<eisy-user>`; not the unrelated "DSC-IT100" in the Beta store) into a free slot.
4. **Configure** `usercode` (and check `host`/`port`), then restart the plugin. Zone nodes appear
   about 5 seconds after it connects.
5. **Move programs and scenes** from the old plugin's nodes (`n004_...`) to the new ones, then
   delete the old EnvisaLink-DSC plugin.

## Rollback

```bash
ssh <pi-user>@<pi-ip> "sudo systemctl disable --now ser2net && sudo systemctl enable --now evl-emu"
```
Then stop or delete the new plugin. The old plugin reconnects on its own.
