"""Async facade over python-OBD.

python-OBD is thread-based and blocking. MCP tool handlers are asyncio
coroutines. The two calls that touch the serial port — `query()` and the
OBD-close inside teardown — are pushed onto the default executor via
`run_in_executor`; the cheap metadata accessors (`supported_commands`,
`supports`, `status`, `protocol_name`, `port_name`) read python-OBD's
in-memory state directly on the loop thread, since they do no serial I/O.

Two locks guard the connection: `_conn_lock` serializes setup/teardown,
and `_io_lock` serializes the executor-side serial I/O. Teardown's
OBD-close runs under `_io_lock`, so it cannot overlap the I/O of an
in-flight `query()` that also holds `_io_lock`. It is *not* full mutual
exclusion: a `query()` whose `_get_connection()` has already returned can
still race a concurrent `close()` — but python-OBD's `query()` on a closed
connection returns a null response rather than raising, so the worst case
is a benign null read, never a crash. In practice `close()` is only called
at lifespan shutdown, so the window is untriggerable in normal operation.
"""

from __future__ import annotations

import asyncio
from types import TracebackType

import obd
from obd import OBD, OBDCommand, OBDResponse, OBDStatus

from obd_mcp.connection import Transport, resolve_transport
from obd_mcp.errors import ObdError, ObdErrorCode


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
        transport: Transport | None = None,
    ) -> None:
        self._portstr = portstr
        self._baudrate = baudrate
        self._timeout = timeout
        self._check_voltage = check_voltage
        self._fast = fast
        self._transport = transport if transport is not None else resolve_transport(portstr)
        self._transport_open = False
        self._connection: OBD | None = None
        self._conn_lock = asyncio.Lock()
        self._io_lock = asyncio.Lock()

    async def _get_connection(self) -> OBD:
        async with self._conn_lock:
            if self._connection is None or not self._connection.is_connected():
                # Pair each transport.open() with a preceding close(): tear down
                # any prior connection + bridge first, so a stateful transport
                # (e.g. a BLE bridge) never has two live opens — even if a prior
                # connect failed after open() returned.
                await self._teardown_locked()
                resolved = await self._transport.open()
                self._transport_open = True
                loop = asyncio.get_running_loop()
                self._connection = await loop.run_in_executor(
                    None,
                    lambda: OBD(
                        portstr=resolved,
                        baudrate=self._baudrate,
                        fast=self._fast,
                        timeout=self._timeout,
                        check_voltage=self._check_voltage,
                    ),
                )
            self._assert_connected(self._connection)
            return self._connection

    async def _teardown_locked(self) -> None:
        """Close the live OBD connection (if any) and the transport bridge.

        Caller holds `_conn_lock`. The OBD close runs under `_io_lock` so it
        can't race an in-flight query() on the same port (conn → io order,
        deadlock-free: query() never takes _conn_lock while holding _io_lock).
        The transport is closed only if it was opened, so this is a safe no-op
        before the first connect and on a repeated close.
        """
        if self._connection is not None:
            conn = self._connection
            self._connection = None
            async with self._io_lock:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, conn.close)
        if self._transport_open:
            self._transport_open = False
            await self._transport.close()

    def _assert_connected(self, conn: OBD) -> None:
        """Map python-OBD post-connect status to the ObdError taxonomy.

        python-OBD swallows pyserial exceptions and hands back a live
        `OBD` object with a text status. We coerce that text into the two
        reachable connection-level ObdError codes (UNABLE_TO_CONNECT,
        BUS_INIT_ERROR); see errors.py.

        CAR_CONNECTED is the single success predicate, deliberately matching
        python-OBD's `is_connected()` (true only for CAR_CONNECTED). If this
        assert accepted a lesser status (ELM_CONNECTED, or OBD_CONNECTED when
        `check_voltage` is on), it would pass while `is_connected()` stays
        false, sending `_get_connection()` into a teardown/reconnect loop.
        """
        status = str(conn.status())
        if status == OBDStatus.CAR_CONNECTED:
            return
        if status == OBDStatus.NOT_CONNECTED:
            raise ObdError(
                ObdErrorCode.UNABLE_TO_CONNECT,
                f"adapter not reachable at {self._portstr}",
            )
        # ELM_CONNECTED (bus init failed) and OBD_CONNECTED (adapter on the bus
        # but the car isn't fully answering, e.g. ignition off) both mean the
        # link is up but unusable for queries.
        raise ObdError(
            ObdErrorCode.BUS_INIT_ERROR,
            f"adapter is alive but the vehicle bus is not fully connected ({status})",
        )

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
            await self._teardown_locked()

    async def __aenter__(self) -> ObdClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()
