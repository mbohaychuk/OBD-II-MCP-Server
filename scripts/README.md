# scripts

Standalone helpers for live-hardware sessions — driving the server's tool
logic against a real ELM327 adapter without going through an MCP host. They
import `obd_mcp` directly and default `OBD_PORT` to the Vgate iCar Pro WiFi
adapter (`socket://192.168.0.10:35000`); override with `OBD_PORT=...`.

Not part of the package or the test suite; kept here as reproducible field
utilities.

| Script | What it does |
|---|---|
| `connect_test.py` | One-shot connection check at the car: prints a readable report and dumps a full JSON snapshot to `/tmp` to share later. |
| `live_read.py` | Read named PIDs (or `--dtcs`) on demand for live back-and-forth. |
| `rev_watch.py` | Sample RPM / throttle / load as fast as the adapter allows for a few seconds (watch a throttle blip). |
| `sensors.py` | Read the O2 / fuel-trim / catalyst PIDs the car supports, plus the emissions self-test monitors. |

```bash
uv run python scripts/connect_test.py
uv run python scripts/live_read.py RPM SPEED THROTTLE_POS
uv run python scripts/live_read.py --dtcs
```
