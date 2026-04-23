"""Async facade over python-OBD.

python-OBD is thread-based and blocking. MCP tool handlers are asyncio
coroutines. Every call into python-OBD is pushed onto the default
executor via `run_in_executor`, and a single `asyncio.Lock` serializes
connection setup and teardown.
"""

from __future__ import annotations

import asyncio
from types import TracebackType

import obd
from obd import OBD, OBDCommand, OBDResponse


class ObdClient:
    """Thin async wrapper around a single `obd.OBD` connection.

    Connection is lazy: the ELM327 handshake runs on the first call that
    needs it, not at construction. A dropped connection is re-established
    on the next call.
    """

    def __init__(
        self,
        portstr: str,
        *,
        baudrate: int = 38400,
        timeout: float = 5.0,
        check_voltage: bool = False,
        fast: bool = False,
    ) -> None:
        self._portstr = portstr
        self._baudrate = baudrate
        self._timeout = timeout
        self._check_voltage = check_voltage
        self._fast = fast
        self._connection: OBD | None = None
        self._conn_lock = asyncio.Lock()
        self._io_lock = asyncio.Lock()

    async def _get_connection(self) -> OBD:
        async with self._conn_lock:
            if self._connection is None or not self._connection.is_connected():
                loop = asyncio.get_running_loop()
                self._connection = await loop.run_in_executor(
                    None,
                    lambda: OBD(
                        portstr=self._portstr,
                        baudrate=self._baudrate,
                        fast=self._fast,
                        timeout=self._timeout,
                        check_voltage=self._check_voltage,
                    ),
                )
            return self._connection

    async def query(self, command: OBDCommand) -> OBDResponse:
        conn = await self._get_connection()
        async with self._io_lock:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, conn.query, command)

    async def supported_commands(self) -> list[OBDCommand]:
        """Every Mode 01 command the ECU advertises support for."""
        conn = await self._get_connection()
        return [c for c in obd.commands.modes[1] if c is not None and conn.supports(c)]

    async def supports(self, command: OBDCommand) -> bool:
        conn = await self._get_connection()
        return bool(conn.supports(command))

    async def is_connected(self) -> bool:
        if self._connection is None:
            return False
        return bool(self._connection.is_connected())

    async def status(self) -> str:
        conn = await self._get_connection()
        return str(conn.status())

    async def protocol_name(self) -> str:
        conn = await self._get_connection()
        return str(conn.protocol_name())

    async def port_name(self) -> str:
        conn = await self._get_connection()
        return str(conn.port_name())

    async def close(self) -> None:
        async with self._conn_lock:
            if self._connection is None:
                return
            conn = self._connection
            self._connection = None
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, conn.close)

    async def __aenter__(self) -> ObdClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()
