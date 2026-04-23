# obd-mcp — Implementation Plan

**Goal.** A Python MCP server that bridges any MCP host (Claude Desktop, Cursor, Mechanics Sidekick) to a live OBD-II port via an ELM327 adapter. Published to Smithery + mcp.so. Paired with a repair-knowledge RAG (Mechanics Sidekick, a separate project) for grounded diagnostic conversations.

**Status.** Phase 2 tool surface complete against the Ircama simulator. `read_freeze_frame`, NHTSA vPIC VIN enrichment, and the five-code error taxonomy are wired. Two Phase-2 items remain pending a garage session and cannot be closed from the desk: legacy-protocol validation on the 2006 A8, and the Bluetooth-classic path (F-150 or Edge over rfcomm). 61 tests green; ruff / mypy strict / stdio handshake clean. Adapter on hand (WiFi clone, Amazon B06XGB4QL7). Dev fleet: 2006 Audi A8 (D3), 2015 F-150, 2019 Ford Edge, 2025 Mustang EcoBoost.

Each decision below has a rationale captured in `DECISIONS.md`. Don't re-litigate without updating that file.

---

## 1. Architecture

```
┌─────────────────┐      stdio       ┌─────────────────────────────┐
│ Claude Desktop  │ ───────────────▶ │ obd-mcp (Python, FastMCP)   │
│ Claude Code     │   JSON-RPC       │                             │
│ Cursor / other  │                  │  ┌───────────────────────┐  │
└─────────────────┘                  │  │ tool handlers (async) │  │
                                     │  └──────────┬────────────┘  │
┌─────────────────┐                  │             │ run_in_executor
│ Mechanics       │ ────── stdio ──▶ │  ┌──────────▼────────────┐  │
│ Sidekick        │                  │  │ ObdClient (threaded)  │  │
│ (MCP host)      │                  │  │ wraps python-OBD      │  │
└─────────────────┘                  │  └──────────┬────────────┘  │
                                     │             │               │
                                     │  ┌──────────▼────────────┐  │
                                     │  │ Connection (pyserial  │  │
                                     │  │   URL abstraction)    │  │
                                     │  │  ├─ /dev/ttyUSB0      │◀── USB ELM327
                                     │  │  ├─ /dev/rfcomm0      │◀── classic BT
                                     │  │  ├─ socket://host:p   │◀── WiFi ELM327
                                     │  │  └─ socket://localhost│◀── Ircama simulator
                                     │  └───────────────────────┘  │
                                     │                             │
                                     │  ┌───────────────────────┐  │
                                     │  │ Knowledge layer       │  │
                                     │  │  ├─ Wal33D DTC DB     │  │  bundled, MIT
                                     │  │  ├─ NHTSA vPIC        │  │  live, public
                                     │  │  ├─ NHTSA TSB/recalls │  │  live, public
                                     │  │  └─ Sidekick RAG (opt)│  │  user-supplied
                                     │  └───────────────────────┘  │
                                     └─────────────────────────────┘
```

**Load-bearing choices:**

- **Transport: stdio** by default. Single-client local hardware bridge. streamable-http only if a remote dashboard becomes in-scope.
- **Server framework: FastMCP** (official MCP Python SDK, v1.27+).
- **ELM327 library: brendan-w/python-OBD**, pinned to a specific commit. Maintainer is absent; be ready to vendor-patch.
- **Threading ↔ asyncio bridge** via `asyncio.get_running_loop().run_in_executor(...)` on every python-OBD call. python-OBD is thread-based; MCP is asyncio.
- **Destructive-op gating: `ctx.elicit()`** (MCP elicitation, 2025-06-18 spec) plus `destructiveHint: true` annotation. Annotations are untrusted hints per spec; elicitation is the runtime confirmation.
- **Simulator: Ircama/ELM327-emulator** via `socket://localhost:35000`. Not writing our own.

## 2. Tool surface

Purpose-built tools, not a raw command passthrough. The ergonomics differentiate us from existing hackathon-tier prior art (castlebbs's `send_elm327_command` passthrough).

| Tool | Annotations | Purpose |
|---|---|---|
| `get_vehicle_info()` | readOnly, idempotent | VIN, calibration IDs, ECU name, connected protocol, adapter voltage. Enriched with NHTSA vPIC → year/make/model/engine. |
| `list_supported_pids()` | readOnly, idempotent | Probes ECU; returns only PIDs this vehicle supports, with decoded names + units. |
| `read_live_data(pids: list[str])` | readOnly | Snapshot read. Decoded values + units + timestamp. |
| `record_session(duration_s, pids, hz_target)` | readOnly, long-running | Streams progress via `ctx.report_progress`. Returns timeseries + resource URI for replay. |
| `read_dtcs(scope="all")` | readOnly, idempotent | scope ∈ {stored, pending, permanent, all}. Joined with Wal33D DB for descriptions. |
| `read_freeze_frame(frame_index=0)` | readOnly, idempotent | Mode 02 sensor snapshot at DTC-set moment. |
| `read_readiness_monitors()` | readOnly, idempotent | Emissions monitor completion status. Required pre-check for `clear_dtcs`. |
| `decode_dtc(code, year?, make?, model?)` | readOnly, idempotent | Wal33D + per-make OBDb JSON for manufacturer-specific codes. |
| `lookup_recalls_and_complaints(year, make, model)` | readOnly, idempotent | NHTSA public API. (TSBs + investigations are not publicly served; see DECISIONS.) |
| `lookup_repair_info(dtc, year, make, model)` | readOnly, idempotent | Optional passthrough to a user-configured Mechanics Sidekick endpoint. |
| `clear_dtcs()` | destructive, requires elicitation | Runs `read_readiness_monitors` first and surfaces incomplete-monitor warning in the elicit prompt. |

## 3. Phased roadmap

### Phase 0 — Setup (1 day)

**Acceptance:** `python -m obd_mcp` launches, Claude Desktop connects via stdio, simulator answers a Mode 01 PID round-trip.

- Python 3.11+ project via `uv` or `poetry`. `pyproject.toml`.
- Ircama `ELM327-emulator` installed; verify `obd.OBD("socket://localhost:35000")` returns a connection.
- FastMCP hello-world tool (`ping()` returns "pong").
- Claude Desktop `mcp.json` pointing at `uv run obd-mcp`.
- `.gitignore`, dev deps (pytest, ruff, mypy).

### Phase 1 — MVP (1 week)

**Acceptance:** Claude Desktop asks "what's wrong with my car?" → reads DTCs against the simulator → returns decoded code(s). Works end-to-end against both simulator and the WiFi clone on any of the three Ford vehicles.

Tools: `get_vehicle_info`, `list_supported_pids`, `read_live_data`, `read_dtcs`, `clear_dtcs` (with elicitation).

- Connection abstraction with three paths (serial / rfcomm / TCP) all collapsed to pyserial URL strings.
- threading→asyncio bridge wrapper around python-OBD.
- Wal33D DTC DB vendored as SQLite (`data/dtc.sqlite`).
- `clear_dtcs` calls `read_readiness_monitors` first, surfaces incomplete-monitor warning in elicit prompt.
- Integration test suite running against Ircama simulator via `elm -n 35000` spawned as a pytest fixture.
- Manual smoke test against the 2015 F-150 at minimum.

### Phase 2 — Completeness (1 week)

**Acceptance:** Full Mode 01 coverage on the Mustang and Edge. Freeze frames readable. Legacy testing done on 2006 A8. Structured error taxonomy surfaced to the LLM.

- `read_freeze_frame`, `read_readiness_monitors`.
- Full Mode 01 PID coverage (populate from `list_supported_pids` result).
- NHTSA vPIC VIN-enrichment in `get_vehicle_info`.
- Error taxonomy: `NO_DATA`, `BUS_INIT_ERROR`, `CAN_ERROR`, `UNABLE_TO_CONNECT`, `ADAPTER_TIMEOUT` — each a structured MCP error response, not a Python exception trace.
- 2006 A8 validation pass. Document any module that needs a custom header.
- Bluetooth-classic path validated (F-150 or Edge, whichever pairs most easily with the dev laptop).

### Phase 3 — Differentiation (1–2 weeks)

**Acceptance:** Demo video recorded on 2025 Mustang with genuine adapter. Server live on Smithery and mcp.so. Sidekick-optional integration working.

- `record_session` with `ctx.report_progress` streaming.
- `lookup_recalls_and_complaints` via NHTSA API.
- `lookup_repair_info` — optional, wired to a user-configured Sidekick endpoint (URL in env var, absent = tool not registered).
- Per-make manufacturer-specific PID decoding using `github.com/OBDb/*` JSON signal sets for Ford (covers 3 of 4 dev vehicles). A8 left on generic Mode 01.
- README with installation, supported hosts, troubleshooting, demo video embed.
- Published to PyPI, Smithery, mcp.so.

**Gate before recording demo:** upgrade to OBDLink CX or EX (~$60). Clone latency is visibly sluggish on camera.

### Phase 4 — Polish (optional, ~3 days)

- Expanded Ircama scenario library (healthy / misfire / cat-efficiency / O2-lazy / MAF-drift). Integration tests parameterize across scenarios.
- Codecov + GH Actions CI.
- Contributor docs (`CONTRIBUTING.md`).

### Phase 5 — Sidekick MCP-host upgrade (separate repo, 1–3 days)

**Acceptance:** Sidekick's terminal chat loop consumes obd-mcp and can answer "my car's throwing P0420, what should I check?" by combining the RAG corpus with live vehicle data.

- Add MCP client to Sidekick (Python MCP SDK client code).
- Wire tool-use loop: Sidekick's model can call obd-mcp tools alongside its existing RAG retrieval.
- 30-second demo clip: "Sidekick reading my car."

A Vue web UI on Sidekick is a further-future decision, not a blocker.

## 4. Hero demo (Phase 3 deliverable)

Recorded against the 2025 Mustang EcoBoost with a genuine OBDLink adapter. Target length ~3 minutes.

1. **Open Claude Desktop.** "Why is my check engine light on?"
2. Claude calls `read_dtcs` → returns e.g. `P0300 - Random/Multiple Cylinder Misfire Detected`. Human-readable explanation.
3. Claude calls `read_freeze_frame` → shows RPM / speed / coolant temp / fuel trims at the moment the DTC set. "The misfire happened at operating temp, mid-throttle, cruising."
4. Claude calls `lookup_repair_info` against Sidekick (if configured), grounding the diagnostic narrative in the actual service manual. "The Mustang service manual lists these common causes for P0300..."
5. Claude calls `read_live_data` on fuel-trim and O2-voltage PIDs to narrow the candidate list.
6. **"I replaced the spark plugs, clear the code."** Claude calls `clear_dtcs`. Elicitation dialog appears with the readiness-monitor warning. User confirms. Code cleared.

The demo proves: tool composition, grounded diagnosis, safety gating. That's the whole story.

## 5. Known risks

| Risk | Mitigation |
|---|---|
| python-OBD maintainer is absent (last release April 2025). | Pin commit; vendor-patch in `third_party/` if needed. |
| Clone WiFi adapter latency/reliability. | Use for dev. Upgrade to OBDLink CX or EX before Phase 3 demo. |
| 2006 A8 may have modules on K-line requiring custom headers. | Phase 2 task, not a blocker — document workarounds. |
| MCP elicitation support varies across hosts. Claude Desktop supports it; some third-party hosts may not. | If a host lacks elicitation, `clear_dtcs` falls back to refusing and directing the user to a supported host. Never degrade silently to unconfirmed clearing. |
| castlebbs hackathon prior art exists. | Differentiation is: maintained, rich purpose-built tool surface, safety-first, registry-published. Call it out in README. |

## 6. Open items

- [ ] Pick OBDLink model (CX vs EX) and order before Phase 3 demo recording.
- [ ] Decide whether to bundle a subset of `OBDb/*` JSONs in-repo or fetch at runtime (leaning bundle for offline use).
- [ ] Verify 2006 A8 protocol (CAN vs K-line) with adapter + simulator first contact.
- [ ] Confirm Claude Desktop's current elicitation UX — version-dependent.
