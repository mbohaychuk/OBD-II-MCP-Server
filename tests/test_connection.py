"""Unit tests for the OBD_PORT transport-resolution seam.

The seam lets a future BLE backend (and auto-detection) slot in without
touching ObdClient. These tests pin the contract: today everything resolves
to a passthrough, and ObdClient opens its connection through whatever
transport it's given.
"""

from __future__ import annotations

import obd
import pytest

from obd_mcp.client import ObdClient
from obd_mcp.connection import PassthroughTransport, Transport, resolve_transport


def test_resolve_socket_url_is_passthrough() -> None:
    assert isinstance(resolve_transport("socket://192.168.0.10:35000"), PassthroughTransport)


def test_resolve_serial_paths_are_passthrough() -> None:
    assert isinstance(resolve_transport("/dev/ttyUSB0"), PassthroughTransport)
    assert isinstance(resolve_transport("/dev/rfcomm0"), PassthroughTransport)


@pytest.mark.asyncio
async def test_passthrough_open_returns_portstr_unchanged() -> None:
    t = PassthroughTransport("socket://localhost:35000")
    assert await t.open() == "socket://localhost:35000"
    await t.close()  # no-op; must not raise


class _RecordingTransport(Transport):
    def __init__(self, portstr: str) -> None:
        self._portstr = portstr
        self.opened = 0
        self.closed = 0

    async def open(self) -> str:
        self.opened += 1
        return self._portstr

    async def close(self) -> None:
        self.closed += 1


class _FakeOBD:
    last_portstr: str | None = None

    def __init__(self, *, portstr: str, **_kwargs: object) -> None:
        type(self).last_portstr = portstr

    def is_connected(self) -> bool:
        return True

    def status(self) -> str:
        return obd.OBDStatus.CAR_CONNECTED

    def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_client_opens_connection_through_its_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ObdClient must resolve the portstr via the transport, not OBD_PORT
    directly, and tear the transport down on close()."""
    monkeypatch.setattr("obd_mcp.client.OBD", _FakeOBD)
    transport = _RecordingTransport("socket://resolved:9999")

    client = ObdClient(portstr="ble://ignored-because-transport-injected", transport=transport)
    await client.status()  # forces _get_connection

    assert transport.opened == 1
    assert _FakeOBD.last_portstr == "socket://resolved:9999"

    await client.close()
    assert transport.closed == 1


@pytest.mark.asyncio
async def test_client_defaults_to_resolved_transport() -> None:
    """With no transport injected, ObdClient resolves one from OBD_PORT."""
    client = ObdClient(portstr="socket://localhost:35000")
    assert isinstance(client._transport, PassthroughTransport)
    assert await client._transport.open() == "socket://localhost:35000"
