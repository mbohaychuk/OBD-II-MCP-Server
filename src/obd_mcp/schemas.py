"""Typed response shapes for the MCP tool surface.

FastMCP turns a tool's return annotation into the `outputSchema` the client
sees, and then validates each result against it. Annotating the thin server
wrappers with these TypedDicts gives clients real field-level types instead of
the empty `{additionalProperties: true}` a bare `dict[str, Any]` produces.

The pure-logic functions in `tools.py` keep returning plain dicts; these only
describe their shape. Each TypedDict must mirror its function's output exactly
— a missing key would be silently dropped from structured content, and an
unexpected one would fail validation — so they are kept honest by
`tests/test_output_schemas.py`, which checks real outputs against them. Fields
whose value is genuinely open (NHTSA payloads, serialized PID values) are typed
`Any` deliberately.
"""

from __future__ import annotations

from typing import Any, TypedDict


class LiveReading(TypedDict):
    pid: str | None
    name: str
    value: Any
    unit: str | None
    error: str | None
    timestamp: float


class SupportedPid(TypedDict):
    pid: str
    name: str
    description: str


class VehicleInfo(TypedDict):
    vin: str | None
    vin_decoded: dict[str, Any] | None
    calibration_ids: Any
    cvn: Any
    voltage_volts: float | None
    protocol: str
    port: str
    status: str
    timestamp: float


class DtcCode(TypedDict):
    code: str
    scope: str
    description: str | None
    source: str | None


class DtcReport(TypedDict):
    scope: str
    count: int
    codes: list[DtcCode]
    timestamp: float


class FreezeFrameDtc(TypedDict):
    code: str
    description: str


class FreezeFrameReading(TypedDict):
    value: Any
    unit: str | None


class FreezeFrame(TypedDict):
    available: bool
    reason: str | None
    dtc: FreezeFrameDtc | None
    frame: dict[str, FreezeFrameReading]
    frame_index: int
    timestamp: float


class Monitor(TypedDict):
    name: str
    applicable: bool
    complete: bool


class ReadinessReport(TypedDict):
    available: bool
    mil: bool | None
    dtc_count: int | None
    ignition_type: str | None
    monitors: list[Monitor]
    timestamp: float


class Sample(TypedDict):
    t: float
    readings: list[LiveReading]


class SessionRecording(TypedDict):
    session_id: str
    resource_uri: str
    duration_s: float
    hz_target: float
    pids: list[str]
    samples_count: int
    samples: list[Sample]
    started_at: float
    ended_early: dict[str, str] | None


class ManufacturerSignal(TypedDict):
    id: str
    name: str
    description: str | None
    unit: str | None
    header: str
    response_address: str | None
    request_hex: str


class ManufacturerSignals(TypedDict):
    available: bool
    reason: str | None
    year: int | None
    make: str
    model: str
    signals: list[ManufacturerSignal]
    timestamp: float


class RecallsAndComplaints(TypedDict):
    year: int
    make: str
    model: str
    recalls: list[dict[str, Any]]
    complaints: list[dict[str, Any]]
    timestamp: float


class ClearDtcsResult(TypedDict):
    cleared: bool
    reason: str | None
    readiness_before: ReadinessReport
    timestamp: float
