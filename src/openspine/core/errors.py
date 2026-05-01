"""Structured error envelope shared across every endpoint.

Per `docs/identity/permissions.md` §"Denial semantics" and `ARCHITECTURE.md` §7,
errors must be machine-readable so agents can self-correct without human
intervention. Every error returned by the API conforms to `ErrorResponse`.

A FastAPI exception handler in `main.py` catches `OpenSpineError` (and its
subclasses), maps to the right HTTP status, and serialises the envelope.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class ErrorResponse(BaseModel):
    """Wire format for every error response. Stable contract for agents."""

    error: str
    domain: str | None = None
    action: str | None = None
    reason: str | None = None
    attempted: dict[str, Any] | None = None
    allowed: dict[str, Any] | None = None
    principal_id: str | None = None
    trace_id: str | None = None
    message: str | None = None


class OpenSpineError(Exception):
    """Base class for every domain-aware error.

    Subclasses set the HTTP status and the structured fields.
    """

    http_status: int = 400
    error_code: str = "openspine_error"

    def __init__(
        self,
        message: str,
        *,
        domain: str | None = None,
        action: str | None = None,
        reason: str | None = None,
        attempted: dict[str, Any] | None = None,
        allowed: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.domain = domain
        self.action = action
        self.reason = reason
        self.attempted = attempted
        self.allowed = allowed

    def to_response(
        self,
        *,
        principal_id: str | None = None,
        trace_id: str | None = None,
    ) -> ErrorResponse:
        return ErrorResponse(
            error=self.error_code,
            domain=self.domain,
            action=self.action,
            reason=self.reason,
            attempted=self.attempted,
            allowed=self.allowed,
            principal_id=principal_id,
            trace_id=trace_id,
            message=self.message,
        )


class ValidationError(OpenSpineError):
    http_status = 422
    error_code = "validation_failed"


class NotFoundError(OpenSpineError):
    http_status = 404
    error_code = "not_found"


class ConflictError(OpenSpineError):
    http_status = 409
    error_code = "conflict"


class AuthenticationError(OpenSpineError):
    http_status = 401
    error_code = "authentication_failed"


class AuthorisationError(OpenSpineError):
    """Raised by the auth-object engine when a check fails.

    Always carries `domain`, `action`, `reason` so agents can reason about the
    denial. See `docs/identity/permissions.md` §"Denial semantics".
    """

    http_status = 403
    error_code = "authorisation_denied"


class SoDViolationError(AuthorisationError):
    error_code = "sod_violation"


class TenantIsolationError(OpenSpineError):
    """A request escaped its tenant scope. Always a programming error."""

    http_status = 500
    error_code = "tenant_isolation_violation"
