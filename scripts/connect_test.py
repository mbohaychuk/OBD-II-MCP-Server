"""Standalone OBD-II connection test — run this AT the car, on the adapter's WiFi.

It connects to the adapter, prints a readable report, and saves a full JSON
snapshot to /tmp that you can hand back to Claude once you're on normal WiFi.

  OBD_PORT defaults to the Vgate iCar Pro WiFi default (socket://192.168.0.10:35000).
  Override:  OBD_PORT=socket://<ip>:<port> uv run python scripts/connect_test.py
"""

import asyncio
import json
import os
import time

from obd_mcp import tools as T
from obd_mcp.client import ObdClient
from obd_mcp.dtc_db import DtcDatabase
from obd_mcp.errors import ObdError

PORT = os.environ.get("OBD_PORT", "socket://192.168.0.10:35000")
LIVE_PIDS = ["RPM", "SPEED", "COOLANT_TEMP", "ENGINE_LOAD", "INTAKE_TEMP", "THROTTLE_POS"]


async def main() -> None:
    print(f"Connecting to {PORT} ...\n")
    client = ObdClient(portstr=PORT, timeout=10.0)
    dtc_db = DtcDatabase()
    out: dict = {"port": PORT, "timestamp": time.time()}

    try:
        info = await T.get_vehicle_info(client)  # also establishes the connection
        out["vehicle_info"] = info
        print("✔ CONNECTED")
        print(f"  protocol : {info.get('protocol')}")
        print(f"  voltage  : {info.get('voltage_volts')} V")
        print(f"  status   : {info.get('status')}")
        print(f"  VIN      : {info.get('vin') or '(not reported)'}")
        vd = info.get("vin_decoded")
        if vd:
            print(f"  vehicle  : {vd.get('year')} {vd.get('make')} {vd.get('model')}")
        else:
            print("  vehicle  : (VIN decode needs internet — skipped on adapter WiFi, that's fine)")

        pids = await T.list_supported_pids(client)
        out["supported_pids"] = pids
        print(f"\n  supported PIDs: {len(pids)}")
        print(f"    e.g. {[p['name'] for p in pids[:8]]}")

        live = await T.read_live_data(client, LIVE_PIDS)
        out["live"] = live
        print("\n  live data:")
        for r in live:
            if r.get("error"):
                print(f"    {r['name']:14} ({r['error']})")
            else:
                print(f"    {r['name']:14} {r['value']} {r.get('unit') or ''}".rstrip())

        dtcs = await T.read_dtcs(client, scope="all", dtc_db=dtc_db)
        out["dtcs"] = dtcs
        print(f"\n  trouble codes: {dtcs['count']}")
        for c in dtcs["codes"]:
            print(f"    {c['code']}  {c.get('description') or ''}")

        readiness = await T.read_readiness_monitors(client)
        out["readiness"] = readiness
        if readiness.get("available"):
            incomplete = [m["name"] for m in readiness["monitors"] if not m["complete"]]
            print(
                f"\n  readiness: {len(readiness['monitors'])} monitors, "
                f"{len(incomplete)} incomplete"
            )

    except ObdError as e:
        out["error"] = str(e)
        print(f"✘ {e}\n")
        code = getattr(e, "code", None)
        if str(code) == "UNABLE_TO_CONNECT":
            print("  -> Can't reach the adapter. Checklist:")
            print("     - Is your laptop joined to the adapter's WiFi (SSID 'V-LINK')?")
            print("     - Is the adapter's LED on? Key to position II (ignition on).")
            print("     - Is the IP right? Try:  ip route   (the 'default via' is the adapter)")
            print("       then re-run with:  OBD_PORT=socket://<ip>:35000")
        elif str(code) == "BUS_INIT_ERROR":
            print("  -> Reached the adapter, but the car's bus didn't answer.")
            print("     - Turn the key to position II (ignition on); engine off is fine.")
            print("     - Give it a few seconds and re-run; some adapters need a warm-up.")
    finally:
        await client.close()
        dtc_db.close()

    fname = f"/tmp/obd_connect_{int(out['timestamp'])}.json"
    with open(fname, "w") as f:
        json.dump(out, f, indent=2, default=str)
    print(f"\nSaved full snapshot to {fname}")
    print("Rejoin your normal WiFi and share that file with me to dig into the results.")


asyncio.run(main())
