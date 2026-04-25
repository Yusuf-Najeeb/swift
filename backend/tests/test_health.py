"""Smoke tests for the FastAPI app created in backend.main."""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.main import create_app


def test_health_endpoint() -> None:
    app = create_app()
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body == {"status": "ok", "service": "Swift Writer"}


def test_root_endpoint() -> None:
    app = create_app()
    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    body = response.json()
    assert body["service"] == "Swift Writer"
    assert body["docs"] == "/docs"


def test_config_endpoint_exposes_models() -> None:
    app = create_app()
    with TestClient(app) as client:
        response = client.get("/config")

    assert response.status_code == 200
    body = response.json()
    assert body["orchestrator_model"].startswith("anthropic/")
    assert body["writer_model"].startswith("openai/")
    assert "openrouter.ai" in body["openrouter_base_url"]
    # Sanity: we should never leak the API key through this endpoint.
    assert "openrouter_api_key" not in body
