# obd-mcp

An MCP server that bridges any MCP host (Claude Desktop, Cursor, agentic
clients, …) to a live OBD-II port via an ELM327 adapter. Python, stdio
transport, FastMCP. See `docs/PLAN.md` for roadmap and `docs/DECISIONS.md`
for the load-bearing design choices.

Status: Phase 2 tool surface complete against the Ircama simulator —
freeze-frame snapshots, NHTSA vPIC VIN enrichment, and a structured
error taxonomy. Field validation on the 2006 A8 (legacy protocols) and
Bluetooth-classic path are still pending a garage session.

## Quick start

```bash
uv sync
# Point at a vehicle adapter (serial / rfcomm / TCP) via pyserial URL:
export OBD_PORT="socket://192.168.0.10:35000"   # WiFi ELM327 clone
# or: /dev/ttyUSB0, /dev/rfcomm0, socket://localhost:35000 (simulator)
uv run obd-mcp
```

### Claude Desktop

```json
{
  "mcpServers": {
    "obd-mcp": {
      "command": "uv",
      "args": ["--directory", "/absolute/path/to/OBD-II-MCP-Server", "run", "obd-mcp"],
      "env": { "OBD_PORT": "socket://192.168.0.10:35000" }
    }
  }
}
```

If `OBD_PORT` is unset the server defaults to `socket://localhost:35000`,
matching the Ircama ELM327-emulator's default TCP port.

## Tool surface

| Tool | Annotations | Purpose |
|---|---|---|
| `ping` | — | Health check (returns `"pong"`). |
| `get_vehicle_info` | readOnly, idempotent | VIN, calibration IDs, CVN, adapter voltage, protocol, link status. VIN (when present) is enriched via NHTSA vPIC → year/make/model/displacement. |
| `list_supported_pids` | readOnly, idempotent | Mode 01 PIDs the ECU advertises support for. |
| `read_live_data(pids)` | readOnly | Snapshot decode of one or more Mode 01 PIDs by name. |
| `read_dtcs(scope)` | readOnly, idempotent | `scope ∈ {stored, pending, all}`. Joined with the bundled Wal33D DTC DB. |
| `read_freeze_frame(frame_index=0)` | readOnly, idempotent | Mode 02 sensor snapshot at DTC-set moment. `frame_index != 0` returns an in-band `FRAME_INDEX_NOT_SUPPORTED` (rare ECUs, deferred). |
| `read_readiness_monitors` | readOnly, idempotent | Emissions-readiness monitor completion status. |
| `list_manufacturer_signals(make, model, year?)` | readOnly, idempotent | Bundled OBDb Mode 22 signal catalogue (Ford Mustang + F-150 in this release). Metadata only — live Mode 22 reads deferred. |
| `lookup_recalls_and_complaints(year, make, model)` | readOnly, idempotent | NHTSA safety recalls + consumer complaints for the vehicle. TSBs / investigations are not publicly served by NHTSA. |
| `lookup_repair_info(dtc, year?, make?, model?)` | readOnly, idempotent | _Optional._ Registered only when `SIDEKICK_URL` is set. Proxies to a Mechanics Sidekick RAG endpoint for repair-manual context. |
| `clear_dtcs` | **destructive** | Mode 04 erase. Gated by MCP elicitation — prompt surfaces incomplete monitors that will be reset. |

### Error taxonomy

Transport-level failures surface as an MCP tool error whose message is
prefixed with one of:

| Code | Meaning |
|---|---|
| `UNABLE_TO_CONNECT` | Adapter not reachable on the given port URL. |
| `BUS_INIT_ERROR` | ELM327 is alive but could not initialize the CAN/K-line bus. |
| `ADAPTER_TIMEOUT` | Request sent but the adapter did not respond in time. |
| `CAN_ERROR` | Bus-level CAN error surfaced by the adapter. |
| `NO_DATA` | No ECU responded to the request. |

Per-PID `NO_DATA` / `NOT_SUPPORTED` / `UNKNOWN_PID` cases inside
`read_live_data` remain in-band (they are data, not transport failures).

## Development

```bash
uv sync
uv run pytest                 # full suite (spawns Ircama simulator as a fixture)
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

The test suite spawns Ircama's ELM327-emulator on a TCP port for
integration tests — no adapter or vehicle required. Unit tests covering
the DTC DB and the destructive-op gating path run pure in-process.

## Credits

- [brendan-w/python-OBD](https://github.com/brendan-w/python-OBD) — ELM327 + OBD-II decoding library (pinned, see `pyproject.toml`).
- [Ircama/ELM327-emulator](https://github.com/Ircama/ELM327-emulator) — simulator used for CI and local testing.
- [Wal33D/dtc-database](https://github.com/Wal33D/dtc-database) — vendored DTC description database at `src/obd_mcp/data/dtc.sqlite`. MIT-licensed; see `src/obd_mcp/data/dtc.sqlite.LICENSE`.
- [OBDb](https://github.com/OBDb) — vendored per-model Mode 22 signal sets at `src/obd_mcp/data/obdb/ford/{mustang,f-150}.json`. CC-BY-SA-4.0; attribution and pinned commits in `src/obd_mcp/data/obdb/LICENSE`.
