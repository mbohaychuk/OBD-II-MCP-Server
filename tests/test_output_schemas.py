"""Keep the output TypedDicts in `schemas.py` honest against real tool output.

FastMCP validates every tool result against the schema derived from its return
annotation: a key the schema omits is dropped from structured content, and a
missing/typed-wrong key fails validation. So a schema that drifts from its
function silently corrupts the surface. These tests produce a real output for
each tool (simulator-backed where possible, injected mocks otherwise) and check
it conforms exactly — no missing keys, no unexpected keys, types compatible.
"""

from __future__ import annotations

import json
import typing
from typing import Any, Union, get_args, get_origin, get_type_hints

import httpx
import pytest

from obd_mcp import schemas
from obd_mcp.client import ObdClient
from obd_mcp.dtc_db import DEFAULT_DB_PATH, DtcDatabase
from obd_mcp.server import mcp
from obd_mcp.tools import (
    clear_dtcs,
    get_vehicle_info,
    list_manufacturer_signals,
    list_supported_pids,
    lookup_recalls_and_complaints,
    read_dtcs,
    read_freeze_frame,
    read_live_data,
    read_readiness_monitors,
    record_session,
)


@pytest.mark.asyncio
async def test_tools_publish_descriptive_output_schemas() -> None:
    """The dict-returning tools must expose a real object schema with named,
    typed properties — not the empty {additionalProperties: true} a bare
    dict[str, Any] return produces. Guards the structured-output wiring."""
    tools = {t.name: t for t in await mcp.list_tools()}
    expected_props = {
        "get_vehicle_info": {"vin", "vin_decoded", "voltage_volts", "status"},
        "read_dtcs": {"scope", "count", "codes", "timestamp"},
        "read_readiness_monitors": {"available", "monitors", "mil"},
        "record_session": {"session_id", "resource_uri", "samples", "samples_count"},
    }
    for name, must_have in expected_props.items():
        schema = tools[name].outputSchema
        assert schema is not None, name
        props = set(schema.get("properties", {}))
        assert must_have <= props, f"{name}: missing {must_have - props}"


def _conforms(value: Any, hint: Any, path: str) -> list[str]:
    """Return a list of structural mismatches between `value` and type `hint`.

    Mirrors how FastMCP's TypedDict-derived model would treat the value:
    every TypedDict key must be present (no extra, no missing) and each
    field type-compatible. `Any` accepts anything; `X | None` allows null.
    """
    if hint is Any:
        return []
    origin = get_origin(hint)

    if origin is Union:
        args = get_args(hint)
        if value is None and type(None) in args:
            return []
        for candidate in (a for a in args if a is not type(None)):
            if not _conforms(value, candidate, path):
                return []
        return [f"{path}: {value!r} matches none of {args}"]

    if typing.is_typeddict(hint):
        if not isinstance(value, dict):
            return [f"{path}: expected {hint.__name__} dict, got {type(value).__name__}"]
        hints = get_type_hints(hint)
        expected, actual = set(hints), set(value)
        errs = [f"{path}.{k}: MISSING" for k in expected - actual]
        errs += [f"{path}.{k}: UNEXPECTED" for k in actual - expected]
        for k in expected & actual:
            errs += _conforms(value[k], hints[k], f"{path}.{k}")
        return errs

    if origin is list:
        (item_t,) = get_args(hint) or (Any,)
        if not isinstance(value, list):
            return [f"{path}: expected list, got {type(value).__name__}"]
        errs: list[str] = []
        for i, item in enumerate(value):
            errs += _conforms(item, item_t, f"{path}[{i}]")
        return errs

    if origin is dict:
        args = get_args(hint)
        val_t = args[1] if len(args) == 2 else Any
        if not isinstance(value, dict):
            return [f"{path}: expected dict, got {type(value).__name__}"]
        errs = []
        for k, v in value.items():
            errs += _conforms(v, val_t, f"{path}[{k!r}]")
        return errs

    if isinstance(hint, type):
        if hint is float and isinstance(value, (int, float)) and not isinstance(value, bool):
            return []
        if hint is int and isinstance(value, bool):
            return [f"{path}: bool where int expected"]
        if isinstance(value, hint):
            return []
        return [f"{path}: expected {hint.__name__}, got {type(value).__name__} ({value!r})"]

    return []


def _assert_conforms(value: Any, hint: Any) -> None:
    errs = _conforms(value, hint, hint.__name__ if isinstance(hint, type) else "$")
    assert not errs, "schema mismatch:\n  " + "\n  ".join(errs)


# --- simulator-backed tools ---------------------------------------------------


@pytest.mark.asyncio
async def test_get_vehicle_info_conforms(elm_simulator: str) -> None:
    client = ObdClient(portstr=elm_simulator)
    try:
        out = await get_vehicle_info(client)
    finally:
        await client.close()
    _assert_conforms(out, schemas.VehicleInfo)


@pytest.mark.asyncio
async def test_list_supported_pids_conforms(elm_simulator: str) -> None:
    client = ObdClient(portstr=elm_simulator)
    try:
        out = await list_supported_pids(client)
    finally:
        await client.close()
    for row in out:
        _assert_conforms(row, schemas.SupportedPid)


@pytest.mark.asyncio
async def test_read_live_data_rows_conform(elm_simulator: str) -> None:
    client = ObdClient(portstr=elm_simulator)
    try:
        # Mix a success row, an unknown-name row, and a non-readable row so all
        # branches of the uniform shape are checked.
        out = await read_live_data(client, ["RPM", "NOPE", "CLEAR_DTC"])
    finally:
        await client.close()
    for row in out:
        _assert_conforms(row, schemas.LiveReading)


@pytest.mark.asyncio
async def test_read_dtcs_conforms_empty(elm_simulator: str) -> None:
    client = ObdClient(portstr=elm_simulator)
    try:
        out = await read_dtcs(client, scope="all")
    finally:
        await client.close()
    _assert_conforms(out, schemas.DtcReport)


@pytest.mark.asyncio
async def test_read_readiness_monitors_conforms(elm_simulator: str) -> None:
    client = ObdClient(portstr=elm_simulator)
    try:
        out = await read_readiness_monitors(client)
    finally:
        await client.close()
    _assert_conforms(out, schemas.ReadinessReport)


@pytest.mark.asyncio
async def test_read_freeze_frame_conforms_no_frame(elm_simulator: str) -> None:
    client = ObdClient(portstr=elm_simulator)
    try:
        out = await read_freeze_frame(client, frame_index=0)
    finally:
        await client.close()
    _assert_conforms(out, schemas.FreezeFrame)


@pytest.mark.asyncio
async def test_record_session_conforms(elm_simulator: str) -> None:
    client = ObdClient(portstr=elm_simulator)
    try:
        out = await record_session(client, duration_s=0.3, pids=["RPM", "SPEED"], hz_target=4.0)
    finally:
        await client.close()
    _assert_conforms(out, schemas.SessionRecording)


# --- stub / mock-backed tools (no simulator, deterministic) -------------------


class _StubResponse:
    def __init__(self, value: Any, has_messages: bool = True) -> None:
        self.value = value
        self.messages = [object()] if has_messages else []

    def is_null(self) -> bool:
        return self.value is None


class _DtcStubClient:
    def __init__(self, codes: list[tuple[str, str]]) -> None:
        self._codes = codes

    async def query(self, command: Any) -> _StubResponse:
        if command.name == "GET_DTC":
            return _StubResponse(self._codes)
        return _StubResponse(None)


@pytest.mark.asyncio
async def test_read_dtcs_conforms_with_codes_and_make() -> None:
    stub = _DtcStubClient([("P0420", "wire"), ("P1000", "wire")])
    with DtcDatabase(DEFAULT_DB_PATH) as db:
        out = await read_dtcs(stub, scope="stored", dtc_db=db, manufacturer="Ford")  # type: ignore[arg-type]
    _assert_conforms(out, schemas.DtcReport)
    sources = {c["source"] for c in out["codes"]}
    assert sources == {"generic", "manufacturer"}


class _StubStatusTest:
    def __init__(self, available: bool, complete: bool) -> None:
        self.available = available
        self.complete = complete


class _StubStatus:
    def __init__(self) -> None:
        self.MIL = False
        self.DTC_count = 0
        self.ignition_type = "spark"
        from obd.codes import BASE_TESTS, SPARK_TESTS

        for name in BASE_TESTS + SPARK_TESTS:
            if name is not None:
                setattr(self, name, _StubStatusTest(available=True, complete=True))


class _ClearStubClient:
    def __init__(self) -> None:
        self.queries: list[str] = []

    async def query(self, command: Any) -> _StubResponse:
        self.queries.append(command.name)
        if command.name == "STATUS":
            return _StubResponse(_StubStatus())
        return _StubResponse(None, has_messages=True)


@pytest.mark.asyncio
async def test_clear_dtcs_result_conforms() -> None:
    async def approve(_msg: str, _incomplete: list[str]) -> bool:
        return True

    out = await clear_dtcs(_ClearStubClient(), approve)  # type: ignore[arg-type]
    _assert_conforms(out, schemas.ClearDtcsResult)


@pytest.mark.asyncio
async def test_list_manufacturer_signals_conforms_both_branches() -> None:
    present = await list_manufacturer_signals(year=2025, make="Ford", model="Mustang")
    _assert_conforms(present, schemas.ManufacturerSignals)
    assert present["available"] is True and present["signals"]

    absent = await list_manufacturer_signals(year=None, make="Toyota", model="Corolla")
    _assert_conforms(absent, schemas.ManufacturerSignals)
    assert absent["available"] is False


@pytest.mark.asyncio
async def test_lookup_recalls_and_complaints_conforms() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "recalls" in request.url.path:
            return httpx.Response(200, content=json.dumps({"Count": 0, "results": []}).encode())
        return httpx.Response(200, content=json.dumps({"count": 0, "results": []}).encode())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        out = await lookup_recalls_and_complaints(
            year=2025, make="Ford", model="Mustang", http_client=http
        )
    _assert_conforms(out, schemas.RecallsAndComplaints)
