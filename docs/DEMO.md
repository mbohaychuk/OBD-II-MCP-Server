# Hero demo — recording checklist

Phase 3 deliverable: a ~3-minute screen recording on the 2025 Mustang
EcoBoost that proves tool composition, grounded diagnosis, and
safety-gated destructive ops. Target narrative is in `PLAN.md` §4; this
doc is the operational checklist.

## Prerequisites

- [ ] OBDLink CX (or EX) adapter on hand. The WiFi clone (Amazon B06XGB4QL7) is too laggy for camera; clone latency is visibly sluggish per commit 43494ad.
- [ ] 2025 Mustang EcoBoost keys. Engine warm enough that readiness monitors are complete-ish (otherwise the elicit prompt is dominated by "these monitors will reset" noise).
- [ ] Claude Desktop installed with `obd-mcp` wired up in `mcp.json`.
- [ ] Sidekick running locally with `SIDEKICK_URL` in `mcp.json` env, repair corpus loaded.
- [ ] Screen recorder (OBS or macOS screencap) + microphone check.
- [ ] A known DTC on the vehicle. Preferably P0300 (misfire). If none present, induce one by pulling a spark-plug boot briefly — re-seat before recording.

## Pre-flight

1. `uv sync` — ensure deps are up to date.
2. `uv run pytest -q` — full suite must be green before recording.
3. Bench-test the full tool chain against the simulator first:
   ```
   export OBD_PORT=socket://localhost:35000
   elm -n 35000 &      # Ircama emulator
   uv run obd-mcp      # sanity check it starts
   ```
4. Start Claude Desktop, confirm all 12 tools appear in the tool picker (11 + Sidekick if configured).
5. `clear_dtcs` dry-run against simulator — confirm elicitation dialog renders.
6. Swap `OBD_PORT` to the vehicle adapter's URL in `mcp.json`. Restart Claude Desktop.
7. Plug adapter into Mustang OBD port. Key to position II (engine off is fine for most of the flow; start engine only for live-data segments if needed).

## Recording script

Timing is a rough guide — the narrative is the priority, not hitting 3:00 exactly.

| Time | User says | Claude does | Visual |
|---|---|---|---|
| 0:00 | "Why is my check engine light on?" | Calls `read_dtcs(scope="stored")` | Returns `P0300`, joined with DTC DB description. |
| 0:20 | (Claude offers to pull freeze frame) | Calls `read_freeze_frame()` | RPM / speed / coolant temp / fuel trims at DTC-set moment. |
| 0:40 | "What was the engine doing?" | Narrates the freeze-frame — e.g. "misfire was at operating temp, mid-throttle, cruising." | — |
| 1:00 | (Claude grounds the diagnosis in repair manual) | Calls `lookup_repair_info(dtc="P0300", year=2025, make="Ford", model="Mustang")` | Sidekick RAG returns summary + citations to the Mustang service manual. |
| 1:30 | "What would narrow it down?" | Calls `read_live_data(pids=["SHORT_FUEL_TRIM_1", "LONG_FUEL_TRIM_1", "O2_B1S1"])` | Live fuel-trim + O2 voltage. |
| 2:00 | "Any recalls on this vehicle I should know about?" | Calls `lookup_recalls_and_complaints(year=2025, make="Ford", model="Mustang")` | 4 recalls surfaced (transmission valve body, LED driver modules, BCM corrosion, EGR valve — all real as of 2026-04). |
| 2:30 | "I replaced the spark plugs — clear the code." | Calls `clear_dtcs`. Elicitation dialog appears. | Dialog shows readiness-monitor warning. User confirms. Code cleared. |
| 2:55 | "Done." | — | |

## Post-record

- [ ] Watch the raw clip. Re-record if audio clips, if the adapter hung, or if the LLM narration drifted off-topic.
- [ ] Trim to ~3:00. Keep the elicitation dialog prominent — that's the safety-first story.
- [ ] Upload to a private link first; embed in `README.md` under a new `## Demo` section only after a sanity pass.
- [ ] Update `README.md` status line: remove "Demo video: TBD".

## Fallbacks if something goes wrong

- Adapter won't connect: check the **Troubleshooting** section in the README.
- No DTC present: skip the `clear_dtcs` segment; record it separately against the simulator with a pre-loaded scenario, splice in. Be explicit in the voiceover that the clear step is simulator footage.
- Sidekick timeout: the tool's in-band error envelope is itself a demo point — narrate the graceful degradation. But retry first; a flaky segment distracts from the narrative.
