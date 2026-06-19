# obd-mcp — Implementation Plan

**Goal.** A Python MCP server that bridges any MCP host (Claude Desktop, Cursor, Mechanics Sidekick) to a live OBD-II port via an ELM327 adapter. Published to Smithery + mcp.so. Paired with a repair-knowledge RAG (Mechanics Sidekick, a separate project) for grounded diagnostic conversations.

**Status.** Phase 3 tool surface complete against the Ircama simulator and hardened. 11 tools: `record_session` (MCP resource replay at `obd://sessions/{id}.json`), `list_manufacturer_signals` (OBDb Ford signal sets bundled), and `lookup_recalls_and_complaints` (NHTSA public API). **Release unblocked** — depends on PyPI `obd==0.7.3` (byte-identical to the old commit pin), version 0.1.0, wheel metadata PyPI-valid (see `docs/RELEASE.md` and the 2026-06-16 DECISIONS entry). 116 tests green; ruff / mypy strict clean; GitHub Actions CI added. Remaining Phase-3 gate: record the hero demo on the 2025 Mustang once the OBDLink CX arrives (`docs/DEMO.md`). Phase-2 field work still open: legacy-protocol validation on the 2006 A8, Bluetooth-classic path. Publishing to PyPI / Smithery / mcp.so awaits a token + go-ahead.

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
                                     │  │  └─ NHTSA TSB/recalls │  │  live, public
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
| `ping()` | readOnly, idempotent | Health check; returns `"pong"`. |
| `get_vehicle_info()` | readOnly, idempotent, openWorld | VIN, calibration IDs, CVN, connected protocol, adapter voltage, link status. Enriched with NHTSA vPIC → year/make/model/displacement. |
| `list_supported_pids()` | readOnly, idempotent | Probes ECU; returns only PIDs this vehicle supports, with decoded names + units. |
| `read_live_data(pids: list[str])` | readOnly | Snapshot read of Mode 01/09 PIDs. Uniform rows (value/unit/error). Decoded values + units + timestamp. |
| `record_session(duration_s, pids, hz_target)` | **not** readOnly (persists a session) | Streams progress via `ctx.report_progress`. Returns timeseries + resource URI for replay. |
| `read_dtcs(scope="all", make?)` | readOnly, idempotent | scope ∈ {stored, pending, all}. Joined with Wal33D DB; each code carries a `source` (generic/manufacturer/wire). Pass `make` to resolve manufacturer-range codes. (Permanent / Mode 0A deferred.) |
| `read_freeze_frame(frame_index=0)` | readOnly, idempotent | Mode 02 sensor snapshot at DTC-set moment. |
| `read_readiness_monitors()` | readOnly, idempotent | Emissions monitor completion status. Required pre-check for `clear_dtcs`. |
| `list_manufacturer_signals(make, model, year?)` | readOnly, idempotent | Bundled OBDb Mode 22 signal catalogue (Ford Mustang + F-150); metadata only, live Mode 22 reads deferred. |
| `lookup_recalls_and_complaints(year, make, model)` | readOnly, idempotent, openWorld | NHTSA public API. (TSBs + investigations are not publicly served; see DECISIONS.) |
| `clear_dtcs()` | destructive, requires elicitation | Runs `read_readiness_monitors` first and surfaces incomplete-monitor warning in the elicit prompt. Refuses with `elicitation_unsupported` if the host can't confirm. |

(The DTC-description join planned as a standalone `decode_dtc` tool was folded into `read_dtcs`.)

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
- Error taxonomy: connection-level failures (`UNABLE_TO_CONNECT`, `BUS_INIT_ERROR`) surface as a structured `[CODE]`-prefixed MCP error, not a Python trace. Per-PID outcomes (`NO_DATA` / `NOT_SUPPORTED` / `UNKNOWN_PID`) stay in-band as data. Adapter-timeout / CAN-error codes deferred until hardware-validated (see DECISIONS).
- 2006 A8 validation pass. Document any module that needs a custom header.
- Bluetooth-classic path validated (F-150 or Edge, whichever pairs most easily with the dev laptop).

### Phase 3 — Differentiation (1–2 weeks)

**Acceptance:** Demo video recorded on 2025 Mustang with genuine adapter. Server live on Smithery and mcp.so.

- `record_session` with `ctx.report_progress` streaming. Timeseries exposed via MCP resource `obd://sessions/{id}.json` (in-memory store; dies with server).
- `lookup_recalls_and_complaints` via NHTSA API.
- Per-make manufacturer-specific PID metadata using `github.com/OBDb/*` JSON signal sets. Bundled: Ford Mustang + F-150 (Edge has no OBDb repo, A8 out of scope). Live Mode 22 reads deferred — see DECISIONS.
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
4. The host (e.g. Mechanics Sidekick) grounds the diagnosis in the service manual with its *own* repair-knowledge tool — `obd-mcp` provides the vehicle data, the host provides the manuals. "The Mustang service manual lists these common causes for P0300..."
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

- [x] ~~Pick OBDLink model (CX vs EX)~~ — CX, per DECISIONS 2026-04-22.
- [x] ~~Bundle a subset of `OBDb/*` JSONs vs fetch at runtime~~ — bundled for offline use (Mustang + F-150), per DECISIONS 2026-04-23.
- [ ] Verify 2006 A8 protocol (CAN vs K-line) with adapter + simulator first contact.
- [ ] Confirm Claude Desktop's current elicitation UX — version-dependent.
