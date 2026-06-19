"""Client-layer error mapping.

Uses real socket URLs — no external network. Ports are chosen to be
deliberately unreachable / unused so python-OBD's status output maps
through to the `ObdError` taxonomy.
"""

from __future__ import annotations

import obd
import pytest
from obd import OBDStatus

from obd_mcp.client import ObdClient
from obd_mcp.errors import ObdError, ObdErrorCode


class _FakeStatusConn:
    """Stub connection reporting a fixed python-OBD status string, so the
    status → ObdError mapping is exercised without a real adapter."""

    def __init__(self, status: str) -> None:
        self._status = status

    def status(self) -> str:
        return self._status


def test_assert_connected_maps_elm_connected_to_bus_init_error() -> None:
    """ELM alive but bus dead → BUS_INIT_ERROR. The most likely real-world
    failure on a cheap clone, otherwise only reachable with hardware."""
    client = ObdClient(portstr="socket://unused")
    with pytest.raises(ObdError) as exc_info:
        client._assert_connected(_FakeStatusConn(OBDStatus.ELM_CONNECTED))  # type: ignore[arg-type]
    assert exc_info.value.code is ObdErrorCode.BUS_INIT_ERROR


def test_assert_connected_maps_not_connected_to_unable_to_connect() -> None:
    client = ObdClient(portstr="socket://unused")
    with pytest.raises(ObdError) as exc_info:
        client._assert_connected(_FakeStatusConn(OBDStatus.NOT_CONNECTED))  # type: ignore[arg-type]
    assert exc_info.value.code is ObdErrorCode.UNABLE_TO_CONNECT


def test_assert_connected_maps_obd_connected_to_bus_init_error() -> None:
    """OBD_CONNECTED (adapter on the bus, car not fully answering) must raise,
    not pass: is_connected() is true only for CAR_CONNECTED, so letting it
    through the assert would diverge from the reconnect predicate and loop."""
    client = ObdClient(portstr="socket://unused")
    with pytest.raises(ObdError) as exc_info:
        client._assert_connected(_FakeStatusConn(OBDStatus.OBD_CONNECTED))  # type: ignore[arg-type]
    assert exc_info.value.code is ObdErrorCode.BUS_INIT_ERROR


def test_assert_connected_passes_when_car_connected() -> None:
    client = ObdClient(portstr="socket://unused")
    client._assert_connected(_FakeStatusConn(OBDStatus.CAR_CONNECTED))  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_unreachable_port_query_raises_unable_to_connect() -> None:
    """query() against an unreachable adapter → UNABLE_TO_CONNECT (not silent null)."""
    client = ObdClient(portstr="socket://127.0.0.1:1", timeout=1.0)
    try:
        with pytest.raises(ObdError) as exc_info:
            await client.query(obd.commands.RPM)
        assert exc_info.value.code is ObdErrorCode.UNABLE_TO_CONNECT
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_unreachable_port_status_raises_unable_to_connect() -> None:
    """All connection-gated methods surface the same mapped error."""
    client = ObdClient(portstr="socket://127.0.0.1:1", timeout=1.0)
    try:
        with pytest.raises(ObdError) as exc_info:
            await client.status()
        assert exc_info.value.code is ObdErrorCode.UNABLE_TO_CONNECT
    finally:
        await client.close()
