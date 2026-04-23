# Decisions

Append-only log of load-bearing design decisions. Newest first. When reversing a decision, add a new entry citing the old one — don't edit history.

Format: **Context · Decision · Why**.

---

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
**Why.** 28,220 codes (9,415 generic + 18,805 manufacturer-specific across 33 brands), MIT, last push Feb 2026. Best license + coverage + maintenance combination found.

## 2026-04-22 — Repair-knowledge RAG: user-supplied via Mechanics Sidekick

**Context.** No legal, redistributable repair-manual corpus exists. AllData/Mitchell1/Identifix are paywalled and hostile to indie use.
**Decision.** `lookup_repair_info` is an optional tool that proxies to a user-configured Sidekick endpoint. Tool is not registered if the endpoint env var is absent. Sidekick itself expects the user to load their own manuals.
**Why.** "User brings their own manuals" is the cleanest legal posture possible. Makes obd-mcp shippable with no content-license baggage.
