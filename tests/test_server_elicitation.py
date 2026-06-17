"""The clear_dtcs consent mapping: only an explicit accept + confirm approves.

This guards the destructive-action gate at the server-wiring layer. The
tools-level clear_dtcs logic is covered in test_tools_dtc_gating.py; here we
pin the elicitation-result → bool mapping that decides whether Mode 04 runs,
since a bug there is a silent consent bypass.
"""

from __future__ import annotations

from mcp.server.elicitation import (
    AcceptedElicitation,
    CancelledElicitation,
    DeclinedElicitation,
)

from obd_mcp.server import _ClearDtcsConfirmation, _elicitation_approved


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
