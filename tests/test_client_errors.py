"""Client-layer error mapping.

Uses real socket URLs — no external network. Ports are chosen to be
deliberately unreachable / unused so python-OBD's status output maps
through to the `ObdError` taxonomy.
"""

from __future__ import annotations

import obd
import pytest

from obd_mcp.client import ObdClient
from obd_mcp.errors import ObdError, ObdErrorCode


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
