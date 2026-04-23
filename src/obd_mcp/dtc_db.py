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
        the upper-case values stored in the DB.
        """
        code_norm = code.upper()
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
                (code_norm, manufacturer.upper(), locale),
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
