"""Read-only wrapper around the vendored Wal33D DTC SQLite database.

The DB is opened read-only with `check_same_thread=False` because the MCP
server runs tool handlers on asyncio executor threads. SQLite read-only
access is safe across threads.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from types import TracebackType

DEFAULT_DB_PATH: Path = Path(str(files("obd_mcp") / "data" / "dtc.sqlite"))

# The DB stores some brands under short names while NHTSA vPIC (the usual source
# of `make`, via get_vehicle_info) returns the full marque. Without this the
# manufacturer join silently no-ops for those brands. Keys are upper-cased.
_MAKE_ALIASES: dict[str, str] = {
    "CHEVROLET": "CHEVY",
    "MERCEDES-BENZ": "MERCEDES",
    "MERCEDES BENZ": "MERCEDES",
    "VW": "VOLKSWAGEN",
}


@dataclass(frozen=True)
class DtcDefinition:
    code: str
    manufacturer: str
    description: str
    type_: str
    is_generic: bool


class DtcDatabase:
    def __init__(self, db_path: Path = DEFAULT_DB_PATH) -> None:
        uri = f"file:{db_path}?mode=ro"
        self._conn = sqlite3.connect(uri, uri=True, check_same_thread=False)

    def lookup(
        self, code: str, manufacturer: str | None = None, locale: str = "en"
    ) -> list[DtcDefinition]:
        """Return DTC definitions for `code`, ordered GENERIC first.

        - `manufacturer=None` → GENERIC row only (empty list if code is manufacturer-specific).
        - `manufacturer="FORD"` → GENERIC row (if any) followed by the FORD row (if any).

        Both `code` and `manufacturer` are matched case-insensitively against
        the upper-case values stored in the DB. A handful of full marque names
        (e.g. "Chevrolet", "Mercedes-Benz") are aliased to the DB's short forms.
        """
        code_norm = code.upper()
        if manufacturer is not None:
            mfr_upper = manufacturer.upper()
            manufacturer = _MAKE_ALIASES.get(mfr_upper, mfr_upper)
        if manufacturer is None:
            cursor = self._conn.execute(
                "SELECT code, manufacturer, description, type, is_generic "
                "FROM dtc_definitions "
                "WHERE code = ? AND manufacturer = 'GENERIC' AND locale = ?",
                (code_norm, locale),
            )
        else:
            cursor = self._conn.execute(
                "SELECT code, manufacturer, description, type, is_generic "
                "FROM dtc_definitions "
                "WHERE code = ? AND manufacturer IN ('GENERIC', ?) AND locale = ? "
                "ORDER BY CASE manufacturer WHEN 'GENERIC' THEN 0 ELSE 1 END",
                (code_norm, manufacturer, locale),
            )
        return [
            DtcDefinition(
                code=row[0],
                manufacturer=row[1],
                description=row[2],
                type_=row[3],
                is_generic=bool(row[4]),
            )
            for row in cursor.fetchall()
        ]

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> DtcDatabase:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
