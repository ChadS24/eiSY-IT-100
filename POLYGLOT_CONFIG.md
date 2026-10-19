## DSC IT-100 Configuration

Connects to a DSC IT-100 through a serial-to-TCP bridge (ser2net), speaking the IT-100 protocol directly.

| Key | Value |
|---|---|
| `host` | IP address of the ser2net bridge (e.g. the Raspberry Pi) |
| `port` | TCP port ser2net listens on (default `10001`) |
| `usercode` | 4- or 6-digit alarm code, used to arm (when the panel asks) and to disarm |
| `partitions` | Number of partition nodes (1-8, default `1`) |
| `zones` | `auto` (zones with a custom keypad label), a count such as `32`, or a list such as `1-32,40` |

Zone nodes are named `Z<n> - <label>` from the panel's keypad labels when they are first created.
Renaming a node in the Admin Console afterwards is kept. Use **Re-read Zone Labels** on the panel
node to add nodes for newly labelled zones.
