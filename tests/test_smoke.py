"""Smoke tests — the bare minimum that CI verifies.

These tests don't need any infrastructure (no Postgres, no Redis, no Qdrant).
They just confirm the package imports, the FastAPI app constructs, and the
health endpoint is wired. Real integration tests land alongside the modules
they cover.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from openspine import __version__
from openspine.main import app


def test_version_string() -> None:
    assert isinstance(__version__, str)
    assert __version__.startswith("0.")


def test_health_endpoint() -> None:
    with TestClient(app) as client:
        response = client.get("/system/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__


def test_readiness_endpoint_shape() -> None:
    with TestClient(app) as client:
        response = client.get("/system/readiness")
    assert response.status_code == 200
    body = response.json()
    assert "dependencies" in body
    assert set(body["dependencies"].keys()) == {"postgres", "redis", "qdrant", "ollama"}


def test_hooks_endpoint_returns_registry_shape() -> None:
    with TestClient(app) as client:
        response = client.get("/system/hooks")
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"pre", "post"}


def test_plugins_endpoint_returns_list() -> None:
    with TestClient(app) as client:
        response = client.get("/system/plugins")
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    # Shape check: each entry has the documented fields
    for entry in body:
        assert {"plugin_id", "package", "state", "loaded_at"} <= set(entry.keys())


def test_openapi_docs_advertise_module_tags() -> None:
    with TestClient(app) as client:
        response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    tag_names = {tag["name"] for tag in schema.get("tags", [])}
    expected = {
        "system",
        "identity",
        "master-data",
        "finance",
        "controlling",
        "materials",
        "production",
    }
    assert expected.issubset(tag_names)
