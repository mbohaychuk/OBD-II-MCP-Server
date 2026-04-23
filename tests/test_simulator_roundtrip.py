"""Phase 0 acceptance: python-OBD can complete a Mode 01 round-trip
against the Ircama ELM327 emulator.

This pins down the full wire path used by every subsequent phase:
pyserial URL → socket → emulator → ELM327 response parser → decoded value.
"""

from __future__ import annotations

import obd


def test_obd_connects_to_simulator(elm_simulator: str) -> None:
    connection = obd.OBD(
        portstr=elm_simulator,
        baudrate=38400,
        fast=False,
        timeout=5,
        check_voltage=False,
    )
    try:
        assert connection.is_connected(), (
            f"python-OBD failed to connect to simulator at {elm_simulator}; "
            f"status={connection.status()}"
        )
    finally:
        connection.close()


def test_mode_01_rpm_roundtrip(elm_simulator: str) -> None:
    connection = obd.OBD(
        portstr=elm_simulator,
        baudrate=38400,
        fast=False,
        timeout=5,
        check_voltage=False,
    )
    try:
        assert connection.is_connected()
        response = connection.query(obd.commands.RPM)
        assert not response.is_null(), f"RPM query returned null: {response}"
        assert response.value is not None
        # The 'car' scenario reports a running engine; RPM should be positive.
        assert response.value.magnitude > 0
    finally:
        connection.close()
