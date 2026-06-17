"""OBD_PORT transport resolution.

`ObdClient` delegates "turn OBD_PORT into a portstr python-OBD can open" to a
`Transport`. Today every supported port — a `socket://host:port` URL for a WiFi
adapter, or a serial / rfcomm device path — is opened directly by python-OBD,
so the resolved transport is a passthrough. The seam exists so a BLE backend (a
bleak → pseudo-terminal bridge) and adapter auto-detection can be added by
extending `resolve_transport` alone, without touching `ObdClient`.
"""

from __future__ import annotations


class Transport:
    """Resolves OBD_PORT to a python-OBD portstr, owning any bridge it starts."""

    async def open(self) -> str:
        """Return a portstr python-OBD can open; start any managed bridge."""
        raise NotImplementedError

    async def close(self) -> None:
        """Tear down any managed bridge. Safe to call when nothing was opened."""
        return None


class PassthroughTransport(Transport):
    """OBD_PORT is already a python-OBD-openable URL or device path; hand it
    through unchanged. python-OBD owns the actual socket/serial lifecycle, so
    there is nothing to tear down here."""

    def __init__(self, portstr: str) -> None:
        self._portstr = portstr

    async def open(self) -> str:
        return self._portstr


def resolve_transport(obd_port: str) -> Transport:
    """Pick the transport for an OBD_PORT value.

    Every value is a passthrough today — `socket://` URLs and device paths are
    opened directly by python-OBD. Future schemes (e.g. `ble://`) add their
    branch here and return a managed transport; nothing else has to change.
    """
    return PassthroughTransport(obd_port)
