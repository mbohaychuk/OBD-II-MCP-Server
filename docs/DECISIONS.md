# Decisions

Append-only log of load-bearing design decisions. Newest first. When reversing a decision, add a new entry citing the old one — don't edit history.

Format: **Context · Decision · Why**.

---

## 2026-06-19 — DTC description resolution: SAE-range-aware, make-specific opt-in

**Context.** The bundled Wal33D DB holds 9,390 manufacturer-specific rows across 33 brands, but `read_dtcs` only ever joined the GENERIC row, so make-specific codes were unreachable. Threading a `make` through raised two subtleties: (1) NHTSA vPIC (the usual `make` source) returns full marques like "Chevrolet"/"Mercedes-Benz" while the DB stores "CHEVY"/"MERCEDES", so a naive join silently no-ops; (2) for a generic-range code (e.g. P0420) the canonical SAE definition is better than a manufacturer's elaboration, but for a manufacturer-range code (e.g. P1xxx) the same code means different things per make, so the make's row is authoritative.
**Decision.** `read_dtcs(make=…)` resolves descriptions by SAE J2012 code range: the second character selects authority (0/2 = ISO/SAE generic, 1/3 = manufacturer). For a generic-range code the GENERIC row wins; for a manufacturer-range code the make-specific row wins when a `make` is supplied, else it falls back to whatever GENERIC row exists, then the wire text. Each code reports a `source` of generic/manufacturer/wire. A small alias map (CHEVROLET→CHEVY, MERCEDES-BENZ→MERCEDES, VW→VOLKSWAGEN) normalizes full marques to the DB's short names. With no `make`, behavior is unchanged (generic-only) — manufacturer rows stay opt-in.
**Why.** Range-awareness is the standard, data-independent way to decide whose definition is canonical, and it avoids degrading generic codes (preferring the manufacturer row outright returned Audi's wording for P0420 over the SAE text). The alias map is what makes the feature actually fire for the brands NHTSA spells differently — without it the headline "manufacturer codes decode" claim would be silently false for several makes.

## 2026-06-18 — Remove `lookup_repair_info` / Sidekick coupling — `obd-mcp` stays consumer-agnostic

**Context.** The 2026-04-23 entry added a `lookup_repair_info` tool that, when `SIDEKICK_URL` was set, proxied DTC lookups to a Mechanics Sidekick RAG endpoint. That baked knowledge of a specific consumer (Sidekick) and of repair-manual RAG into `obd-mcp` — contradicting the project's own composability stance (a dumb OBD-II reader that knows nothing about who's calling it; cf. the 2026-04-22 "no bespoke UI on obd-mcp" entry).
**Decision.** Remove it entirely: delete `sidekick.py`, the `lookup_repair_info` tool, the `register_sidekick_tool` wiring, the `SIDEKICK_URL` env var, and their tests/docs. `obd-mcp` exposes only OBD-II reader tools. Repair-knowledge lookup is the **host's** job — a host (Mechanics Sidekick, Claude Desktop, …) holds `obd-mcp`'s tools alongside its own document/RAG tools, and the calling LLM decides which to invoke. The tool surface drops from 12 (11 + the conditional one) to a flat 11.
**Why.** A reader that knows about its consumer isn't composable. Manuals, RAG, and Sidekick are orchestration concerns that belong to the agent/host, not the OBD-II data source. Removing the coupling sharpens the boundary the whole project rests on, and makes the planned Sidekick-as-MCP-host integration (PLAN Phase 5) cleaner — Sidekick consumes `obd-mcp` like any other tool provider, with no back-channel. Reverses the 2026-04-23 `lookup_repair_info` contract decision.

## 2026-06-17 — Connection transport seam; BLE backend deferred to hardware validation

**Context.** Connecting a real adapter (Vgate iCar Pro, BLE + WiFi) exposed that `OBD_PORT` setup is painful: a WiFi AP steals the laptop's internet, classic Bluetooth needs a manual `rfcomm bind`, and BLE needs an external `ble-serial` bridge to a pseudo-terminal. python-OBD only opens serial / `socket://` URLs; BLE (GATT, no serial profile) can't be handed to it directly. We want obd-mcp to own the connection so setup is "turn the adapter on", with adapter auto-detection later.
**Decision.** Introduce a `Transport` seam in `connection.py`: `resolve_transport(OBD_PORT) -> Transport`, where `Transport.open()` returns a python-OBD portstr and owns any bridge it starts. Today everything resolves to `PassthroughTransport` (socket:// and device paths are opened by python-OBD unchanged); `ObdClient` opens through the transport and tears it down on `close()`. A `ble://` backend (bleak → PTY bridge) and an `auto` probe loop then become additive changes to `resolve_transport` alone. The BLE backend itself is NOT built yet — BLE ELM327 GATT service/characteristic UUIDs and write semantics vary per adapter, so they must be discovered and validated against the real iCar Pro, not guessed.
**Why.** The seam is the reusable foundation; building it now (passthrough only) is fully testable and keeps `ObdClient` untouched when BLE / auto-detect land. Deferring the BLE backend to a hardware session matches the project's rule against shipping hardware code we can't validate (cf. deferred Mode 22 reads, freeze-frame index). The new `bleak` dependency arrives with the BLE backend, not now.

## 2026-06-16 — python-OBD dependency: PyPI `obd==0.7.3`, not the git-URL pin

**Context.** `pyproject.toml` pinned `obd @ git+...@a378bdd8`; PyPI rejects direct-URL dependencies, which `RELEASE.md` framed as a release blocker needing either an upstream PyPI release or vendoring (the 2026-04-22 fallback). An audit found the awaited release already exists: commit `a378bdd8` is byte-identical to python-OBD's `v0.7.3` tag (GitHub compare: identical, 0 ahead/0 behind), and `obd 0.7.3` was published to PyPI on 2025-04-07, minutes after the commit.
**Decision.** Depend on `obd==0.7.3` from PyPI (exact pin, preserving the frozen-behaviour intent of the commit pin) and drop the hatch `allow-direct-references`. Do not vendor or fork. The built wheel's `Requires-Dist` is now a plain PyPI specifier with no direct URL, and the full suite passes unchanged against the PyPI artifact.
**Why.** The maintainer-absent risk that justified pinning a commit is fully covered by an immutable PyPI release of the same bits, at zero cost. Vendoring takes on GPLv2 bundling and permanent maintenance for no benefit when `0.7.3` is one specifier away. This keeps the standard `pip install obd-mcp` path that the Smithery / mcp.so listings point at, and closes the `RELEASE.md` §0 blocker. Supersedes the "vendor as fallback" half of the 2026-04-22 python-OBD pin decision.

## 2026-06-16 — Error taxonomy pruned to reachable codes; timeout/CAN deferred

**Context.** The 2026-04-23 error-taxonomy entry promised five `[CODE]`-prefixed transport errors. An audit found only `UNABLE_TO_CONNECT` and `BUS_INIT_ERROR` are ever raised: python-OBD swallows adapter timeouts and CAN faults into a null `OBDResponse` rather than an exception, so `ADAPTER_TIMEOUT`, `CAN_ERROR`, and a transport-level `NO_DATA` were unreachable dead codes that the README and PLAN advertised to the LLM as real behavior.
**Decision.** Reduce `ObdErrorCode` to the two reachable connection-level codes. Keep `NO_DATA` / `NOT_SUPPORTED` / `UNKNOWN_PID` as in-band per-PID markers (plain strings in `read_live_data`), documented separately as data. Defer real adapter-timeout / CAN-error mapping until it can be detected from raw ELM327 reply tokens and validated against a custom Ircama scenario plus real hardware.
**Why.** Advertising error codes the code cannot produce is a silent lie to the LLM (and the user) — the same reasoning as the 2026-04-23 `lookup_tsbs_and_recalls` → `lookup_recalls_and_complaints` rename. Honest-now beats aspirational; the richer mapping returns as a hardware-gated task, not a permanently-broken promise. This reverses the five-code surface of the 2026-04-23 entry; the `[CODE]`-prefix mechanism itself is unchanged.

## 2026-04-23 — `record_session` storage: in-memory dict, MCP resource template

**Context.** Phase 3 plan promised `record_session` returns "timeseries + resource URI for replay". Three options: in-memory only (dies with server), JSONL on disk ($XDG_DATA_HOME), inline-only no resource URI.
**Decision.** In-memory `_SESSIONS: dict[str, dict]` on the server module. MCP resource template `obd://sessions/{session_id}.json` serves the stored payload. FastMCP's resource handlers don't receive lifespan context, so the dict is module-level (mcp server is a singleton per process — lifetimes match). No disk write, no cache eviction, no TTL.
**Why.** Replay-within-a-session is the realistic use case — the MCP host talks to this process for the duration of a conversation. Persistence across restarts adds filesystem/permissions surface without a concrete demand. The record tool returns the full samples inline *and* populates the resource, so the LLM can choose: keep the data in tool-response context or fetch via resource URI if it's dropped its working memory.

## 2026-04-23 — OBDb Ford signal sets: bundled (CC-BY-SA-4.0), Mode 22 reads deferred

**Context.** PLAN.md §6 left open "bundle vs fetch" for OBDb signal sets. OBDb repos are CC-BY-SA-4.0 (content, not software). Of the dev fleet, OBDb has Ford-Mustang and Ford-F-150 but not Ford-Edge; the A8 is out of scope.
**Decision.** Bundle `Ford-Mustang` and `Ford-F-150` JSONs at pinned commits under `src/obd_mcp/data/obdb/ford/`. Add a separate `LICENSE` file in that directory declaring the CC-BY-SA-4.0 obligation (attribution + share-alike on downstream modifications of the JSON). The Python code is not a derivative of the JSON — different license boundary, same pattern as a GPL dependency vs your application code. Live Mode 22 reads (send `22 XXXX`, decode per `fmt`) deferred — validating without a vehicle + genuine adapter is not safe.
**Why.** Offline-first wins for a hardware bridge: network-required startup is a non-starter for a tool that might run without internet. Live reads need real-vehicle coverage we don't have yet; shipping metadata-only still gives LLMs useful context ("on a 2025 Mustang, LPFP duty-cycle is PID 0307") without the risk of misdecoding bytes we've never seen on the wire.

## 2026-04-23 — Sidekick `lookup_repair_info` contract: `POST /repair-lookup`, env-var gated

**Context.** PLAN.md says `lookup_repair_info` is an optional tool proxying to a user-configured Mechanics Sidekick endpoint. Three contract details were open: HTTP verb/path, request body, registration behavior when the env var is absent.
**Decision.** `POST {SIDEKICK_URL}/repair-lookup` with JSON body `{dtc, year, make, model}` (year/make/model nullable). Response `{summary: string?, sources: [{title, url, excerpt}]}`. If `SIDEKICK_URL` is unset, the tool is not registered at all — it doesn't appear in `tools/list`. Any Sidekick failure (network, non-200, bad JSON) collapses to `{available: false, error, summary: null, sources: []}` with the caller's context echoed back.
**Why.** Registering a placeholder tool that always returns "not configured" wastes LLM tool-selection attention. Opt-in-by-env is the cleanest deployment posture. The response envelope mirrors `vin_decoded` / NHTSA lookup patterns: best-effort, never raises, the LLM can narrate outages.

## 2026-04-23 — Tool renamed: `lookup_tsbs_and_recalls` → `lookup_recalls_and_complaints`

**Context.** Phase 3 plan named this tool `lookup_tsbs_and_recalls`. Probing NHTSA's public API showed only `/recalls/recallsByVehicle` and `/complaints/complaintsByVehicle` answer unauthenticated; `/investigations/investigationsByVehicle` and `/bulletins/*` return "Missing Authentication Token". TSB bulletins are manufacturer-copyrighted and not published via public API.
**Decision.** Rename the tool to match what the API actually serves. Recalls + complaints are the two endpoints available; leave TSB-style repair knowledge to the optional `lookup_repair_info` → Sidekick passthrough.
**Why.** Naming a tool after content we can't deliver is a silent lie to the LLM (and therefore the user). A TSB-shaped prompt becomes a no-op at runtime. Scope to what exists.

## 2026-04-23 — Error taxonomy surfaces as `[CODE] message` exception string

**Context.** Phase 2 promises a "structured error response, not a Python exception trace" for connection-level failures. MCP's `CallToolResult.isError=true` shape has only text content — no reserved field for an error code.
**Decision.** Raise `ObdError(code, message)` where `str(err) == "[CODE] message"`. FastMCP forwards the exception string verbatim (no traceback). LLMs key off the `[CODE]` prefix.
**Why.** Zero dependency on MCP SDK internals, works today, round-trips through any MCP host. Per-PID errors inside `read_live_data` stay in-band — they are data, not transport failures.

## 2026-04-23 — `read_freeze_frame` supports frame_index=0 only

**Context.** Mode 02 on the wire takes a frame-index byte per request (`02 <PID> <FRAME>`); python-OBD's command set only emits `02 <PID>` (implicitly frame 0).
**Decision.** Accept `frame_index != 0` as input but return an in-band `{available: False, reason: "FRAME_INDEX_NOT_SUPPORTED"}`. No raw-command shim until a vehicle on hand requires it.
**Why.** Multi-frame ECUs are uncommon. Building a bypass around python-OBD for a hypothetical use case violates YAGNI. In-band error is honest about the limitation without hiding it.

## 2026-04-23 — VIN enrichment: NHTSA vPIC via httpx, best-effort

**Context.** `get_vehicle_info` wants year/make/model. The VIN alone is not human-friendly.
**Decision.** Add `httpx` dep. On each `get_vehicle_info` call with a VIN, hit `vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValues`. Any failure → `vin_decoded: null`, rest of payload unchanged. No caching in v1.
**Why.** vPIC is public, unauthenticated, permissively licensed government data. `httpx` is the async-native choice and already in the mcp dependency tree. Caching is premature — VIN doesn't change within a session, but neither do LLMs call this in a tight loop.

## 2026-04-22 — Hero demo vehicle: 2025 Mustang EcoBoost

**Context.** Four test vehicles on hand; recorded demo needs exactly one.
**Decision.** Mustang for the recorded video. 2006 A8 used in Phase 2 for legacy-protocol robustness testing.
**Why.** Modern CAN = cleanest code path. Visual impact. A8 tells a better *technical* story but would be a worse *sales* artifact for a 3-minute demo.

## 2026-04-22 — Package name: `obd-mcp`

**Context.** Considered brandier names.
**Decision.** `obd-mcp`.
**Why.** Descriptive, greppable on registries, no brand-risk. First-page hit for anyone searching the niche.

## 2026-04-22 — Adapter: keep the clone, upgrade before demo

**Context.** WiFi ELM327 clone already purchased (Amazon B06XGB4QL7).
**Decision.** Use the clone throughout dev. Upgrade to OBDLink CX or EX (~$60) before Phase 3 demo recording.
**Why.** Clone is fine for CAN-based Phase 1–2 work and its limitations are a test asset (graceful degradation against cheap hardware). Clone latency is visibly sluggish on camera — genuine adapter for the recorded demo is worth $60.

## 2026-04-22 — No bespoke UI on obd-mcp

**Context.** Considered a Vue dashboard in-repo.
**Decision.** obd-mcp ships without a UI. Primary UX is any MCP host (Claude Desktop, Cursor, Sidekick). If a Vue dashboard is wanted, it lives inside Mechanics Sidekick as part of that project's own roadmap.
**Why.** Bespoke UI on a protocol server defeats the protocol's win. Composability is the story: "obd-mcp runs anywhere, Sidekick is one consumer of many."

## 2026-04-22 — Sidekick integration is a separate repo/phase

**Context.** Mechanics Sidekick already exists as a terminal RAG chat. Merge vs. keep-separate.
**Decision.** Separate. obd-mcp ships v1.0 standalone. Sidekick gets an MCP-host upgrade as its own project.
**Why.** Two clean resume bullets. Integration is a 1–3 day task (MCP client + tool-use wiring). Coupling would force synchronized releases.

## 2026-04-22 — No custom simulator

**Context.** Original plan included writing an ELM327 protocol simulator as a differentiator.
**Decision.** Use Ircama/ELM327-emulator (MIT, actively maintained as of Feb 2026).
**Why.** Covers AT commands, ISO-TP flow control, KWP2000 sessions, multi-ECU, pty + TCP transport. Matches or exceeds what we'd build in a week.

## 2026-04-22 — Destructive-op gating: elicitation, not just annotations

**Context.** `clear_dtcs` is the only destructive tool. MCP spec (2025-06-18) offers `destructiveHint` annotations and `elicitation/create` requests.
**Decision.** Use `ctx.elicit()` for runtime confirmation. Keep `destructiveHint: true` as belt-and-suspenders for hosts that surface hints in the UI.
**Why.** Spec is explicit: annotations are untrusted hints, not security. Elicitation is the only runtime-enforced confirmation primitive. `clear_dtcs` also resets readiness monitors (emissions implications) — surfacing that in the elicit prompt is a concrete safety win.

## 2026-04-22 — Transport: stdio

**Context.** MCP supports stdio, deprecated SSE, and streamable-http.
**Decision.** stdio default. No HTTP server at all in v1.
**Why.** Single-client local hardware bridge. No auth surface, lowest latency, matches the Claude Desktop launch model (host spawns server as child process). Revisit only if remote dashboard becomes in-scope (not planned).

## 2026-04-22 — Language: Python

**Context.** Considered C# for stack-alignment with existing resume work.
**Decision.** Python.
**Why.** python-OBD handles ELM327 quirks, protocol auto-detect, Mode 01/02/03/04/07/09 decoding for free. FastMCP is the official MCP SDK. A C# equivalent would be 2+ weeks of yak-shaving with no portfolio payoff. The portfolio story is "right tool for the job," not "stretch the existing stack."

## 2026-04-22 — ELM327 library: brendan-w/python-OBD, pinned

**Context.** Candidate libraries: brendan-w/python-OBD (1272 stars, de-facto standard, maintainer absent), py-obdii (modern beta, API unstable), barracuda-fsh/pyobd (application, not library).
**Decision.** brendan-w/python-OBD pinned to a specific commit. Vendor-patch in `third_party/` if bugs block us.
**Why.** Best coverage, widest community, proven. Maintainer absence is a manageable risk given our narrow usage surface.

## 2026-04-22 — DTC database: Wal33D/dtc-database (MIT)

**Context.** SAE J2012 is paywalled. Community DTC datasets vary in coverage and freshness.
**Decision.** Vendor `Wal33D/dtc-database` as a SQLite snapshot in `data/`.
**Why.** 18,805 codes (9,415 generic + 9,390 manufacturer-specific across 33 brands — `SELECT COUNT(*)` on the bundled snapshot; pinned commit in `data/dtc.sqlite.source`), MIT, last push Feb 2026. Best license + coverage + maintenance combination found.

## 2026-04-22 — Repair-knowledge RAG: user-supplied via Mechanics Sidekick

**Context.** No legal, redistributable repair-manual corpus exists. AllData/Mitchell1/Identifix are paywalled and hostile to indie use.
**Decision.** `lookup_repair_info` is an optional tool that proxies to a user-configured Sidekick endpoint. Tool is not registered if the endpoint env var is absent. Sidekick itself expects the user to load their own manuals.
**Why.** "User brings their own manuals" is the cleanest legal posture possible. Makes obd-mcp shippable with no content-license baggage.
