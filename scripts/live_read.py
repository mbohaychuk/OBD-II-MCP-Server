"""Read OBD PIDs (or DTCs) on demand — used for live back-and-forth at the car.

  uv run python scripts/live_read.py RPM SPEED THROTTLE_POS    # read named PIDs now
  uv run python scripts/live_read.py --dtcs                    # read trouble codes
OBD_PORT defaults to socket://192.168.0.10:35000 (Vgate iCar Pro WiFi).
"""

import asyncio
import os
import sys

from obd_mcp import tools as T
from obd_mcp.client import ObdClient
from obd_mcp.dtc_db import DtcDatabase
from obd_mcp.errors import ObdError

PORT = os.environ.get("OBD_PORT", "socket://192.168.0.10:35000")


async def main() -> None:
    args = sys.argv[1:]
    client = ObdClient(portstr=PORT, timeout=10.0)
    try:
        if "--dtcs" in args:
            db = DtcDatabase()
            try:
                d = await T.read_dtcs(client, scope="all", dtc_db=db)
            finally:
                db.close()
            print(f"{d['count']} code(s)")
            for c in d["codes"]:
                print(f"  {c['code']}  {c.get('description') or ''}")
        else:
            pids = args or ["RPM", "SPEED", "COOLANT_TEMP"]
            for r in await T.read_live_data(client, pids):
                if r.get("error"):
                    print(f"{r['name']:16} ({r['error']})")
                else:
                    print(f"{r['name']:16} {r['value']} {r.get('unit') or ''}".rstrip())
    except ObdError as e:
        print(f"ERROR {e}")
    finally:
        await client.close()


asyncio.run(main())
