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
    """Resolves OBD_PORT to a python-OBD portstr, owning any bridge it starts.

    Lifecycle contract for bridge-owning backends (e.g. a future BLE
    transport): ObdClient tears the transport down before every (re)connect, so
    each `open()` is preceded by a `close()` of the previous open — a backend
    must never end up with two live bridges. `close()` must be safe in every
    state: after `open()` returned, after `open()` raised partway (the backend
    must unwind partial state so a later close fully reclaims it), and when
    `open()` was never called.
    """

    async def open(self) -> str:
        """Return a portstr python-OBD can open; (re)start any managed bridge.

        May raise (peripheral not found, bridge spawn failed); on raising it
        must leave nothing half-open that a later `close()` can't reclaim.
        """
        raise NotImplementedError

    async def close(self) -> None:
        """Tear down any managed bridge. Idempotent and safe in every state."""
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
