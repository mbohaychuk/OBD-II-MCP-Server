"""Integration tests for `ObdClient` against the Ircama simulator fixture.

These exercise the threading→asyncio bridge: the python-OBD object lives on
executor threads, but every public method is a coroutine.
"""

from __future__ import annotations

import asyncio

import obd
import pytest

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
