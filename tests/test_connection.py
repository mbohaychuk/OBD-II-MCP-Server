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
        return str(obd.OBDStatus.CAR_CONNECTED)

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


class _StatefulBridgeTransport(Transport):
    """Models a bridge-owning backend (like BLE): open() starts a live bridge,
    close() tears it down. Tracks the high-water mark of simultaneously-live
    bridges so a reconnect that re-opens without closing first is caught."""

    def __init__(self, portstr: str) -> None:
        self._portstr = portstr
        self.live = False
        self.max_concurrent = 0
        self.opens = 0
        self.closes = 0

    async def open(self) -> str:
        self.opens += 1
        self.max_concurrent = max(self.max_concurrent, 2 if self.live else 1)
        self.live = True
        return self._portstr

    async def close(self) -> None:
        self.closes += 1
        self.live = False


class _AlwaysDroppedOBD:
    """is_connected() is always False, forcing _get_connection to reconnect on
    every call so the open/close pairing can be exercised."""

    def __init__(self, *, portstr: str, **_kwargs: object) -> None:
        pass

    def is_connected(self) -> bool:
        return False

    def status(self) -> str:
        return str(obd.OBDStatus.CAR_CONNECTED)

    def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_reconnect_never_double_opens_a_stateful_bridge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reconnect must close the prior bridge before opening a new one, so a
    stateful (BLE-style) transport never has two live opens at once."""
    monkeypatch.setattr("obd_mcp.client.OBD", _AlwaysDroppedOBD)
    transport = _StatefulBridgeTransport("socket://bridge:1")
    client = ObdClient(portstr="ble://x", transport=transport)

    await client.status()  # first connect → open #1
    await client.status()  # reconnect → close #1, then open #2 (never 2 live)
    await client.close()

    assert transport.max_concurrent == 1, "two bridges were live at once on reconnect"
    assert transport.opens == 2
    assert transport.closes == 2
    assert transport.live is False


@pytest.mark.asyncio
async def test_close_before_any_connect_does_not_touch_transport() -> None:
    """Nothing was opened, so close() must not call into the transport."""
    transport = _RecordingTransport("socket://x:1")
    client = ObdClient(portstr="ble://x", transport=transport)
    await client.close()
    assert transport.opened == 0
    assert transport.closed == 0


@pytest.mark.asyncio
async def test_double_close_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """A second close() after teardown must not re-close the transport."""
    monkeypatch.setattr("obd_mcp.client.OBD", _FakeOBD)
    transport = _RecordingTransport("socket://x:1")
    client = ObdClient(portstr="ble://x", transport=transport)
    await client.status()  # opens the transport
    await client.close()
    await client.close()
    assert transport.opened == 1
    assert transport.closed == 1
