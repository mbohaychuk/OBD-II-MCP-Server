"""The clear_dtcs consent mapping: only an explicit accept + confirm approves.

This guards the destructive-action gate at the server-wiring layer. The
tools-level clear_dtcs logic is covered in test_tools_dtc_gating.py; here we
pin the elicitation-result → bool mapping that decides whether Mode 04 runs,
since a bug there is a silent consent bypass.
"""

from __future__ import annotations

from typing import Any

import pytest
from mcp.server.elicitation import (
    AcceptedElicitation,
    CancelledElicitation,
    DeclinedElicitation,
)

from obd_mcp.server import _ClearDtcsConfirmation, _elicitation_approved, _make_clear_confirm
from obd_mcp.tools import ElicitationUnsupported


def test_accepted_with_confirm_true_approves() -> None:
    result = AcceptedElicitation(data=_ClearDtcsConfirmation(confirm=True))
    assert _elicitation_approved(result) is True


def test_accepted_with_confirm_false_refuses() -> None:
    result = AcceptedElicitation(data=_ClearDtcsConfirmation(confirm=False))
    assert _elicitation_approved(result) is False


def test_declined_refuses() -> None:
    assert _elicitation_approved(DeclinedElicitation()) is False


def test_cancelled_refuses() -> None:
    assert _elicitation_approved(CancelledElicitation()) is False


class _FakeSession:
    def __init__(self, supports: bool) -> None:
        self._supports = supports

    def check_client_capability(self, _capability: Any) -> bool:
        return self._supports


class _FakeCtx:
    """Just enough Context for the confirmer: a session with a capability check
    and an elicit() that returns a canned result."""

    def __init__(self, supports: bool, elicit_result: Any = None) -> None:
        self.session = _FakeSession(supports)
        self._elicit_result = elicit_result

    async def elicit(self, message: str, schema: Any) -> Any:
        return self._elicit_result


async def test_confirm_raises_when_host_cannot_elicit() -> None:
    """The capability gate runs only in production; pin that an
    elicitation-incapable host raises ElicitationUnsupported before elicit()."""
    confirm = _make_clear_confirm(_FakeCtx(supports=False))  # type: ignore[arg-type]
    with pytest.raises(ElicitationUnsupported):
        await confirm("clear?", [])


async def test_confirm_elicits_and_maps_acceptance_when_supported() -> None:
    accepted = AcceptedElicitation(data=_ClearDtcsConfirmation(confirm=True))
    confirm = _make_clear_confirm(_FakeCtx(supports=True, elicit_result=accepted))  # type: ignore[arg-type]
    assert await confirm("clear?", []) is True

    declined = AcceptedElicitation(data=_ClearDtcsConfirmation(confirm=False))
    confirm = _make_clear_confirm(_FakeCtx(supports=True, elicit_result=declined))  # type: ignore[arg-type]
    assert await confirm("clear?", []) is False
