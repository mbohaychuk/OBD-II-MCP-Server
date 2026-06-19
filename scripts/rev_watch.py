"""Sample RPM / throttle / load as fast as the adapter allows for a few seconds
so we can watch a throttle blip. OBD_PORT defaults to the iCar Pro WiFi."""

import asyncio
import os
import time

from obd_mcp import tools as T
from obd_mcp.client import ObdClient

PORT = os.environ.get("OBD_PORT", "socket://192.168.0.10:35000")
DURATION = float(os.environ.get("DUR", "12"))
PIDS = ["RPM", "THROTTLE_POS", "ENGINE_LOAD"]


async def main() -> None:
    client = ObdClient(portstr=PORT, timeout=10.0)
    try:
        await T.read_live_data(client, ["RPM"])  # warm up the link
        start = time.time()
        peak_rpm = 0.0
        n = 0
        print(f"{'t(s)':>5}  {'RPM':>7}  {'thr%':>6}  {'load%':>6}")
        while time.time() - start < DURATION:
            rows = {r["name"]: r for r in await T.read_live_data(client, PIDS)}
            t = time.time() - start
            rpm = rows.get("RPM", {}).get("value")
            thr = rows.get("THROTTLE_POS", {}).get("value")
            load = rows.get("ENGINE_LOAD", {}).get("value")
            if isinstance(rpm, (int, float)):
                peak_rpm = max(peak_rpm, rpm)
            n += 1
            print(f"{t:5.1f}  {rpm!s:>7}  {thr!s:>6}  {load!s:>6}")
        print(f"\nsamples: {n}   peak RPM: {peak_rpm}")
    finally:
        await client.close()


asyncio.run(main())
