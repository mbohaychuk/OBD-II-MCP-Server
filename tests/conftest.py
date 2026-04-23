"""Shared pytest fixtures.

The `elm_simulator` fixture spawns Ircama's ELM327-emulator as a TCP server
so tests can point `obd.OBD("socket://localhost:PORT")` at it without
needing a real adapter or vehicle.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from collections.abc import Iterator

import pytest

SIMULATOR_PORT = 35000
SIMULATOR_HOST = "localhost"
SIMULATOR_URL = f"socket://{SIMULATOR_HOST}:{SIMULATOR_PORT}"


def _wait_for_port(host: str, port: int, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except (ConnectionRefusedError, OSError):
            time.sleep(0.1)
    return False


@pytest.fixture(scope="session")
def elm_simulator() -> Iterator[str]:
    """Spawn the ELM327 emulator (scenario 'car') on a TCP port; yield its URL."""
    # stdin must stay open — the emulator's interactive CLI exits on EOF.
    proc = subprocess.Popen(
        [sys.executable, "-m", "elm", "-n", str(SIMULATOR_PORT), "-s", "car"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        if not _wait_for_port(SIMULATOR_HOST, SIMULATOR_PORT):
            proc.kill()
            stdout, stderr = proc.communicate(timeout=2)
            pytest.fail(
                "ELM327 emulator did not open port "
                f"{SIMULATOR_PORT} within 10s.\n"
                f"stdout: {stdout.decode(errors='replace')}\n"
                f"stderr: {stderr.decode(errors='replace')}"
            )
        yield SIMULATOR_URL
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)
