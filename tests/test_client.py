"""Integration tests for `ObdClient` against the Ircama simulator fixture.

These exercise the threading→asyncio bridge: the python-OBD object lives on
executor threads, but every public method is a coroutine.
"""

from __future__ import annotations

import asyncio
import threading

import obd
import pytest
from obd import OBDStatus

from obd_mcp.client import ObdClient


@pytest.mark.asyncio
async def test_lazy_connect_then_query(elm_simulator: str) -> None:
    client = ObdClient(portstr=elm_simulator)
    try:
        # No connection attempt before first call.
        assert not await client.is_connected()
        resp = await client.query(obd.commands.RPM)
        assert not resp.is_null(), f"RPM was null: {resp}"
        assert resp.value is not None
        assert resp.value.magnitude > 0
        assert await client.is_connected()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_protocol_and_port_name_populated(elm_simulator: str) -> None:
    client = ObdClient(portstr=elm_simulator)
    try:
        # Trigger connection.
        await client.query(obd.commands.RPM)
        assert await client.protocol_name()  # e.g. "ISO 15765-4 (CAN 11/500)"
        assert await client.port_name() == elm_simulator
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_supported_commands_includes_rpm(elm_simulator: str) -> None:
    client = ObdClient(portstr=elm_simulator)
    try:
        cmds = await client.supported_commands()
        assert obd.commands.RPM in cmds
        assert obd.commands.SPEED in cmds
        # Every entry is actually a Mode 01 command.
        assert all(c.mode == 1 for c in cmds)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_concurrent_queries_return_independent_results(elm_simulator: str) -> None:
    client = ObdClient(portstr=elm_simulator)
    try:
        rpm, speed, coolant = await asyncio.gather(
            client.query(obd.commands.RPM),
            client.query(obd.commands.SPEED),
            client.query(obd.commands.COOLANT_TEMP),
        )
        assert not rpm.is_null()
        assert not speed.is_null()
        assert not coolant.is_null()
        assert rpm.command == obd.commands.RPM
        assert speed.command == obd.commands.SPEED
        assert coolant.command == obd.commands.COOLANT_TEMP
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_close_and_reconnect(elm_simulator: str) -> None:
    client = ObdClient(portstr=elm_simulator)
    try:
        await client.query(obd.commands.RPM)
        assert await client.is_connected()
        await client.close()
        assert not await client.is_connected()
        resp = await client.query(obd.commands.RPM)
        assert not resp.is_null()
    finally:
        await client.close()


class _BlockingConn:
    """Fake python-OBD connection whose query() blocks until released, so we
    can deterministically interleave close() with an in-flight query()."""

    def __init__(self) -> None:
        self.events: list[str] = []
        self._log_lock = threading.Lock()
        self.release = threading.Event()
        self.query_started = threading.Event()

    def is_connected(self) -> bool:
        return True

    def status(self) -> str:
        return OBDStatus.CAR_CONNECTED

    def query(self, _command: object) -> str:
        self._record("query_start")
        self.query_started.set()
        self.release.wait(2.0)
        self._record("query_end")
        return "resp"

    def close(self) -> None:
        self._record("close")

    def _record(self, event: str) -> None:
        with self._log_lock:
            self.events.append(event)


@pytest.mark.asyncio
async def test_close_waits_for_in_flight_query() -> None:
    """close() must not tear down the connection while a query() is mid-flight
    on the same serial port — both run real I/O and must be serialized."""
    client = ObdClient(portstr="socket://unused")
    fake = _BlockingConn()
    client._connection = fake  # type: ignore[assignment]

    query_task = asyncio.create_task(client.query(obd.commands.RPM))
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, fake.query_started.wait, 2.0)

    close_task = asyncio.create_task(client.close())
    await asyncio.sleep(0.05)
    # close() must be blocked behind the in-flight query's I/O lock.
    assert "close" not in fake.events

    fake.release.set()
    await asyncio.gather(query_task, close_task)
    assert fake.events == ["query_start", "query_end", "close"]
