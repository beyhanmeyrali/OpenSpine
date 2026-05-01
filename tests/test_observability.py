"""Tests for the observability surface."""

from __future__ import annotations

from fastapi.testclient import TestClient

from openspine.core.observability import (
    auth_decisions_total,
    embedding_indexed_total,
    events_published_total,
    metrics_response_body,
)
from openspine.main import app


def test_metrics_endpoint_returns_prometheus_format() -> None:
    with TestClient(app) as client:
        response = client.get("/metrics")
    assert response.status_code == 200
    body = response.text
    # Prometheus exposition format starts with HELP/TYPE comments.
    assert "# HELP openspine_http_requests_total" in body
    assert "# TYPE openspine_http_requests_total counter" in body


def test_metrics_endpoint_not_in_openapi_schema() -> None:
    with TestClient(app) as client:
        schema = client.get("/openapi.json").json()
    assert "/metrics" not in schema.get("paths", {})


def test_request_metrics_increment_on_health_call() -> None:
    with TestClient(app) as client:
        client.get("/system/health")
        client.get("/system/health")
    body = metrics_response_body()[0].decode()
    assert 'openspine_http_requests_total{method="GET"' in body
    assert 'route="/system/health"' in body


def test_domain_counters_register_without_error() -> None:
    # The metrics module declares these — make sure they're constructed
    # against the dedicated registry (no DuplicateError on import).
    events_published_total.labels(stream="md.material.created").inc()
    embedding_indexed_total.labels(collection="t-test").inc()
    auth_decisions_total.labels(domain="fi.invoice", action="post", decision="allow").inc()

    body = metrics_response_body()[0].decode()
    assert "openspine_events_published_total" in body
    assert "openspine_embedding_indexed_total" in body
    assert "openspine_auth_decisions_total" in body
