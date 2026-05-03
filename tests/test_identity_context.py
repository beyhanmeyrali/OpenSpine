"""Unit tests for the principal-context dataclass + middleware helpers.

These exercise the pure-function paths (header parsing, dataclass
construction). Full middleware behaviour requires a real Postgres
session and is covered in tests/integration/.
"""

from __future__ import annotations

import uuid

from fastapi import Request

from openspine.identity.context import PrincipalContext
from openspine.identity.middleware import _ensure_trace_id, _extract_bearer


def _make_request(headers: dict[str, str], cookies: dict[str, str] | None = None) -> Request:
    """Build a Starlette Request shell sufficient for header/cookie reads."""
    raw_headers = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    if cookies:
        cookie_header = "; ".join(f"{k}={v}" for k, v in cookies.items())
        raw_headers.append((b"cookie", cookie_header.encode()))
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": raw_headers,
        "query_string": b"",
    }
    return Request(scope)


# ---------------------------------------------------------------------------
# PrincipalContext
# ---------------------------------------------------------------------------


def test_anonymous_context_has_no_principal() -> None:
    trace = uuid.uuid4()
    ctx = PrincipalContext.anonymous(trace_id=trace)
    assert ctx.is_anonymous
    assert ctx.principal_id is None
    assert ctx.tenant_id is None
    assert ctx.auth_method == "anonymous"
    assert ctx.trace_id == trace
    assert ctx.effective_roles == []


def test_authenticated_context_round_trips() -> None:
    tenant = uuid.uuid4()
    principal = uuid.uuid4()
    trace = uuid.uuid4()
    ctx = PrincipalContext(
        tenant_id=tenant,
        principal_id=principal,
        principal_kind="human",
        auth_method="session",
        trace_id=trace,
    )
    assert not ctx.is_anonymous
    assert ctx.principal_id == principal
    assert ctx.tenant_id == tenant
    assert ctx.principal_kind == "human"


# ---------------------------------------------------------------------------
# Header parsers
# ---------------------------------------------------------------------------


def test_extract_bearer_returns_token_when_header_present() -> None:
    request = _make_request({"authorization": "Bearer osp_user_abc123"})
    assert _extract_bearer(request) == "osp_user_abc123"


def test_extract_bearer_returns_none_when_header_absent() -> None:
    request = _make_request({})
    assert _extract_bearer(request) is None


def test_extract_bearer_returns_none_when_scheme_wrong() -> None:
    request = _make_request({"authorization": "Basic dXNlcjpwYXNz"})
    assert _extract_bearer(request) is None


def test_extract_bearer_returns_none_when_value_empty() -> None:
    request = _make_request({"authorization": "Bearer  "})
    assert _extract_bearer(request) is None


def test_ensure_trace_id_uses_traceparent_when_well_formed() -> None:
    trace_hex = "4bf92f3577b34da6a3ce929d0e0e4736"
    request = _make_request({"traceparent": f"00-{trace_hex}-00f067aa0ba902b7-01"})
    out = _ensure_trace_id(request)
    assert out == uuid.UUID(trace_hex)


def test_ensure_trace_id_generates_when_traceparent_missing() -> None:
    request = _make_request({})
    out = _ensure_trace_id(request)
    assert isinstance(out, uuid.UUID)


def test_ensure_trace_id_generates_when_traceparent_malformed() -> None:
    request = _make_request({"traceparent": "garbage"})
    out = _ensure_trace_id(request)
    assert isinstance(out, uuid.UUID)


def test_ensure_trace_id_generates_when_trace_id_segment_invalid_hex() -> None:
    request = _make_request(
        {"traceparent": "00-not-a-real-trace-id-as-hex-here-at-all-00f067aa0ba902b7-01"}
    )
    out = _ensure_trace_id(request)
    # Falls back to a fresh UUID rather than raising.
    assert isinstance(out, uuid.UUID)
