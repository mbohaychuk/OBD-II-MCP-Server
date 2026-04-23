# obd-mcp

An MCP server that bridges any MCP host (Claude Desktop, Cursor, agentic
clients, …) to a live OBD-II port via an ELM327 adapter. Python, stdio
transport, FastMCP. See `docs/PLAN.md` for roadmap and `docs/DECISIONS.md`
for the load-bearing design choices.

Status: Phase 1 (MVP) complete against the Ircama simulator; awaiting
real-vehicle validation on the dev fleet.

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

## Phase 1 tool surface

| Tool | Annotations | Purpose |
|---|---|---|
| `ping` | — | Health check (returns `"pong"`). |
| `get_vehicle_info` | readOnly, idempotent | VIN, calibration IDs, CVN, adapter voltage, protocol, link status. |
| `list_supported_pids` | readOnly, idempotent | Mode 01 PIDs the ECU advertises support for. |
| `read_live_data(pids)` | readOnly | Snapshot decode of one or more Mode 01 PIDs by name. |
| `read_dtcs(scope)` | readOnly, idempotent | `scope ∈ {stored, pending, all}`. Joined with the bundled Wal33D DTC DB. |
| `read_readiness_monitors` | readOnly, idempotent | Emissions-readiness monitor completion status. |
| `clear_dtcs` | **destructive** | Mode 04 erase. Gated by MCP elicitation — prompt surfaces incomplete monitors that will be reset. |

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
