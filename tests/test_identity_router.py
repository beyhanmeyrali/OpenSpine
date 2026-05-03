"""Auth-router unit tests — anonymous and validation paths only.

These tests don't need a live database. They exercise:
- /auth/me with no auth (anonymous path through the middleware)
- /auth/login with malformed body (request validation)
- /auth/logout with no cookie (idempotent path)
- protected endpoints without auth (401 envelope)

Round-trip login + token + totp tests need a real Postgres and live
in tests/integration/test_identity_endpoints.py.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from openspine.main import app


def test_me_anonymous_returns_is_anonymous_true() -> None:
    with TestClient(app) as client:
        response = client.get("/auth/me")
    assert response.status_code == 200
    body = response.json()
    assert body["is_anonymous"] is True
    assert body["principal_id"] is None
    assert body["tenant_id"] is None
    assert body["auth_method"] == "anonymous"


def test_login_rejects_missing_fields() -> None:
    with TestClient(app) as client:
        response = client.post("/auth/login", json={})
    assert response.status_code == 422
    body = response.json()
    # FastAPI default validation envelope.
    assert "detail" in body


def test_login_rejects_short_totp_code() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/auth/login",
            json={
                "tenant_slug": "default",
                "username": "u",
                "password": "p",
                "totp_code": "123",
            },
        )
    assert response.status_code == 422


def test_logout_without_cookie_is_idempotent() -> None:
    with TestClient(app) as client:
        response = client.post("/auth/logout")
    assert response.status_code == 204


def test_issue_token_requires_authentication() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/auth/tokens",
            json={"kind": "user_api"},
        )
    assert response.status_code == 401
    body = response.json()
    # Structured-error envelope.
    assert body["error"] == "authentication_failed"
    assert body["domain"] == "auth"
    assert body["reason"] == "not_authenticated"


def test_revoke_token_requires_authentication() -> None:
    with TestClient(app) as client:
        response = client.delete("/auth/tokens/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 401


def test_totp_enrol_requires_authentication() -> None:
    with TestClient(app) as client:
        response = client.post("/auth/totp/enrol")
    assert response.status_code == 401


def test_totp_verify_requires_authentication() -> None:
    with TestClient(app) as client:
        response = client.post("/auth/totp/verify", json={"code": "123456"})
    assert response.status_code == 401


def test_openapi_includes_auth_routes() -> None:
    with TestClient(app) as client:
        response = client.get("/openapi.json")
    schema = response.json()
    paths = set(schema["paths"].keys())
    expected = {
        "/auth/login",
        "/auth/logout",
        "/auth/me",
        "/auth/tokens",
        "/auth/tokens/{token_id}",
        "/auth/totp/enrol",
        "/auth/totp/verify",
    }
    assert expected.issubset(paths)
