from fastapi.testclient import TestClient

from backend.main import create_app


def test_health_ok():
    app = create_app()
    client = TestClient(app)
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_config_requires_token_when_set(monkeypatch):
    monkeypatch.setenv("SWIFT_API_BEARER_TOKEN", "secret")
    from backend.config import get_settings

    get_settings.cache_clear()

    app = create_app()
    client = TestClient(app)

    assert client.get("/config").status_code == 401
    assert client.get("/config", headers={"Authorization": "Bearer nope"}).status_code == 401
    ok = client.get("/config", headers={"Authorization": "Bearer secret"})
    assert ok.status_code == 200
    assert "openrouter_base_url" in ok.json()

