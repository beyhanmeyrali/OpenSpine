"""Tests for the structured error envelope."""

from __future__ import annotations

from openspine.core.errors import (
    AuthorisationError,
    OpenSpineError,
    SoDViolationError,
    ValidationError,
)


def test_validation_error_serialises_with_envelope() -> None:
    err = ValidationError(
        "Turkish tax ID required.",
        domain="md.business_partner",
        action="create",
        reason="missing_tax_id",
        attempted={"country": "TR", "tax_id": None},
    )
    response = err.to_response(principal_id="p-1", trace_id="t-1")
    payload = response.model_dump(exclude_none=True)
    assert payload["error"] == "validation_failed"
    assert payload["domain"] == "md.business_partner"
    assert payload["reason"] == "missing_tax_id"
    assert payload["attempted"] == {"country": "TR", "tax_id": None}
    assert payload["principal_id"] == "p-1"
    assert payload["trace_id"] == "t-1"


def test_authorisation_error_includes_attempted_and_allowed() -> None:
    err = AuthorisationError(
        "Amount exceeds limit.",
        domain="fi.invoice",
        action="post",
        reason="amount_exceeds_limit",
        attempted={"amount": "15000.00", "currency": "EUR"},
        allowed={"max_amount": "10000.00", "currency": "EUR"},
    )
    payload = err.to_response().model_dump(exclude_none=True)
    assert payload["error"] == "authorisation_denied"
    assert payload["allowed"]["max_amount"] == "10000.00"


def test_sod_violation_uses_dedicated_code() -> None:
    err = SoDViolationError("SoD: cannot create vendor and release payment.")
    assert err.error_code == "sod_violation"
    assert err.http_status == 403


def test_base_error_carries_message() -> None:
    err = OpenSpineError("something broke")
    assert str(err) == "something broke"
    assert err.message == "something broke"
