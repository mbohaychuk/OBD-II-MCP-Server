"""OBDb signal-set loader.

Vendored JSONs under `data/obdb/` define manufacturer-specific Mode 22
signals for Ford vehicles in the dev fleet (Mustang, F-150). Files are
pinned to specific OBDb commits — see `data/obdb/LICENSE` for the
attribution + CC-BY-SA-4.0 declaration.

This module only *reads* signal metadata; it does not issue Mode 22
requests to the ECU. Live reads are deferred until we have
genuine-adapter + real-vehicle validation (see DECISIONS.md). Surfacing
the signal catalogue still has standalone value: an LLM can narrate
"on a 2025 Mustang, the LPFP duty-cycle signal at PID 0307 is relevant
here" without the server having to talk to the ECU.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_DATA_DIR = Path(__file__).resolve().parent / "data" / "obdb"

# Canonical lookup keyed by (lowercase make, normalized model).
# Normalization: lowercase, strip dashes/spaces.
_MODEL_FILES: dict[tuple[str, str], Path] = {
    ("ford", "mustang"): _DATA_DIR / "ford" / "mustang.json",
    ("ford", "f150"): _DATA_DIR / "ford" / "f-150.json",
}


@dataclass(frozen=True)
class Signal:
    """One decoded signal from a manufacturer-specific Mode 22 command."""

    id: str
    name: str
    description: str | None
    unit: str | None
    header: str
    response_address: str | None
    request_hex: str


def _normalize_model(model: str) -> str:
    return model.lower().replace("-", "").replace(" ", "").replace("_", "")


def _matches_filter(filter_spec: Any, year: int) -> bool:
    if not isinstance(filter_spec, dict):
        return True
    if "years" in filter_spec and isinstance(filter_spec["years"], list):
        if year in filter_spec["years"]:
            return True
        if "from" not in filter_spec and "to" not in filter_spec:
            return False
    from_year = filter_spec.get("from")
    to_year = filter_spec.get("to")
    if from_year is not None and year < from_year:
        return False
    return not (to_year is not None and year > to_year)


def _build_request_hex(cmd: Any) -> str | None:
    if not isinstance(cmd, dict) or not cmd:
        return None
    mode, pid = next(iter(cmd.items()))
    if not isinstance(mode, str) or not isinstance(pid, str):
        return None
    return (mode + pid).upper()


def load_signals(make: str, model: str, year: int | None = None) -> list[Signal]:
    """Return manufacturer-specific signals for a year/make/model.

    Returns `[]` for unsupported make/model combinations (generic Mode 01
    still works for those — it's handled upstream in `read_live_data`).
    Year filtering uses OBDb's `filter` field semantics: `from`/`to`
    bounds and/or an explicit `years` list. Commands with no filter are
    included unconditionally.
    """
    path = _MODEL_FILES.get((make.lower(), _normalize_model(model)))
    if path is None or not path.exists():
        return []

    with path.open() as f:
        data = json.load(f)

    signals: list[Signal] = []
    for cmd in data.get("commands", []):
        if not isinstance(cmd, dict):
            continue
        if year is not None and not _matches_filter(cmd.get("filter"), year):
            continue
        request_hex = _build_request_hex(cmd.get("cmd"))
        if request_hex is None:
            continue
        header = cmd.get("hdr", "") or ""
        rax = cmd.get("rax") if isinstance(cmd.get("rax"), str) else None
        for sig in cmd.get("signals", []):
            if not isinstance(sig, dict):
                continue
            fmt_raw = sig.get("fmt")
            fmt: dict[str, Any] = fmt_raw if isinstance(fmt_raw, dict) else {}
            signals.append(
                Signal(
                    id=str(sig.get("id", "")),
                    name=str(sig.get("name", "")),
                    description=sig.get("description"),
                    unit=fmt.get("unit"),
                    header=str(header),
                    response_address=rax,
                    request_hex=request_hex,
                )
            )
    return signals
