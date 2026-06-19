"""Read O2 / fuel-trim / sensor PIDs this car actually supports, plus the
emissions self-test monitors (the 'health' signal). iCar Pro WiFi by default."""

import asyncio
import os

from obd_mcp import tools as T
from obd_mcp.client import ObdClient

PORT = os.environ.get("OBD_PORT", "socket://192.168.0.10:35000")
KEYWORDS = ("O2", "FUEL_TRIM", "EQUIV_RATIO", "CATALYST", "MAF", "AIR_STATUS")


async def main() -> None:
    client = ObdClient(portstr=PORT, timeout=10.0)
    try:
        supported = [p["name"] for p in await T.list_supported_pids(client)]
        relevant = [n for n in supported if any(k in n for k in KEYWORDS)]
        print(f"sensor/O2 PIDs this car supports ({len(relevant)}): {relevant}\n")

        print("live readings:")
        for r in await T.read_live_data(client, relevant):
            if r.get("error"):
                print(f"  {r['name']:26} ({r['error']})")
            else:
                print(f"  {r['name']:26} {r['value']} {r.get('unit') or ''}".rstrip())

        readiness = await T.read_readiness_monitors(client)
        print("\nemissions self-test monitors (sensor health):")
        for m in readiness.get("monitors", []):
            mark = "OK" if m["complete"] else "INCOMPLETE"
            print(f"  {m['name']:34} {mark}")
    finally:
        await client.close()


asyncio.run(main())
