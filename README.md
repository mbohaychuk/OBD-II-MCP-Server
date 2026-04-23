# obd-mcp

An MCP server that bridges any MCP host (Claude Desktop, Cursor, agentic
clients, …) to a live OBD-II port via an ELM327 adapter. Python, stdio
transport, FastMCP. See `docs/PLAN.md` for roadmap and `docs/DECISIONS.md`
for the load-bearing design choices.

Status: Phase 3 tool surface complete against the Ircama simulator —
12 tools including `record_session` (with MCP resource replay),
`list_manufacturer_signals` (bundled OBDb Ford signal sets), NHTSA
recalls/complaints, and an optional Sidekick repair-info passthrough.
Field validation on the 2006 A8 (legacy protocols) and Bluetooth-classic
path still pending a garage session. Demo video: TBD (recording on the
2025 Mustang EcoBoost once the OBDLink CX adapter arrives).

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
      "env": {
        "OBD_PORT": "socket://192.168.0.10:35000",
        "SIDEKICK_URL": "http://localhost:8080"
      }
    }
  }
}
```

If `OBD_PORT` is unset the server defaults to `socket://localhost:35000`,
matching the Ircama ELM327-emulator's default TCP port. `SIDEKICK_URL`
is optional — see **Configuration** below.

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `OBD_PORT` | `socket://localhost:35000` | pyserial URL for the adapter. Examples: `/dev/ttyUSB0`, `/dev/rfcomm0`, `socket://192.168.0.10:35000`. |
| `SIDEKICK_URL` | _(unset)_ | Base URL of a Mechanics Sidekick RAG endpoint. If set, the `lookup_repair_info` tool is registered; if unset, the tool is not advertised to the host at all. |

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
| `record_session(duration_s, pids, hz_target)` | readOnly | Time-bounded PID sampling. Streams progress via MCP progress notifications; returns timeseries inline and stores it under the MCP resource URI `obd://sessions/{id}.json` for later replay. In-memory only. |
| `list_manufacturer_signals(make, model, year?)` | readOnly, idempotent | Bundled OBDb Mode 22 signal catalogue (Ford Mustang + F-150 in this release). Metadata only — live Mode 22 reads deferred. |
| `lookup_recalls_and_complaints(year, make, model)` | readOnly, idempotent | NHTSA safety recalls + consumer complaints for the vehicle. TSBs / investigations are not publicly served by NHTSA. |
| `lookup_repair_info(dtc, year?, make?, model?)` | readOnly, idempotent | _Optional._ Registered only when `SIDEKICK_URL` is set. Proxies to a Mechanics Sidekick RAG endpoint for repair-manual context. |
| `clear_dtcs` | **destructive** | Mode 04 erase. Gated by MCP elicitation — prompt surfaces incomplete monitors that will be reset. |

### MCP resources

| Template | Purpose |
|---|---|
| `obd://sessions/{session_id}.json` | JSON payload stored by `record_session`. Lives in memory for the duration of the server process; reading an unknown ID raises a resource error. |

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

## Troubleshooting

**`UNABLE_TO_CONNECT`** — the adapter itself is not reachable.
- For WiFi clones: ensure the adapter's AP is not joined and its LAN IP is routable (default `192.168.0.10:35000`).
- For USB: check `ls /dev/ttyUSB*` (Linux) or `/dev/tty.usbserial*` (macOS); you may need to add your user to `dialout` / `uucp`.
- For Bluetooth-classic: pair the adapter first, then `sudo rfcomm bind /dev/rfcomm0 <mac>`.

**`BUS_INIT_ERROR`** — the ELM327 is talking, but the vehicle bus didn't answer.
- Key must be in position II (ignition on) — engine-off is fine.
- Very cheap clones may need `OBD_PORT` protocol forced; not exposed yet (file an issue if you hit this).
- 2006-era vehicles may be K-line only; legacy protocols not yet validated.

**`ADAPTER_TIMEOUT`** — the adapter accepted the request but didn't reply in time.
- Clone adapters add 100–300ms per query. If you see this during `record_session`, lower `hz_target`.
- USB cables under 1m work best; long cables + clones are a common cause.

**`clear_dtcs` elicitation doesn't appear** — the MCP host may not support elicitation.
- Claude Desktop supports it as of 2025-06-18.
- If unsupported, the tool refuses rather than silently clearing (see `DECISIONS.md`).

**Tests hang or fail on startup** — the Ircama simulator fixture may have a stale process.
- `pkill -f elm327-emulator` and re-run `uv run pytest`.

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
the DTC DB, VIN/NHTSA/Sidekick lookups, OBDb signal loader, and the
destructive-op gating path run pure in-process via `httpx.MockTransport`.

## Credits

- [brendan-w/python-OBD](https://github.com/brendan-w/python-OBD) — ELM327 + OBD-II decoding library (pinned, see `pyproject.toml`).
- [Ircama/ELM327-emulator](https://github.com/Ircama/ELM327-emulator) — simulator used for CI and local testing.
- [Wal33D/dtc-database](https://github.com/Wal33D/dtc-database) — vendored DTC description database at `src/obd_mcp/data/dtc.sqlite`. MIT-licensed; see `src/obd_mcp/data/dtc.sqlite.LICENSE`.
- [OBDb](https://github.com/OBDb) — vendored per-model Mode 22 signal sets at `src/obd_mcp/data/obdb/ford/{mustang,f-150}.json`. CC-BY-SA-4.0; attribution and pinned commits in `src/obd_mcp/data/obdb/LICENSE`.
- NHTSA vPIC + recalls/complaints APIs — public, unauthenticated.
