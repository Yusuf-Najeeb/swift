from pathlib import Path

from fastapi.testclient import TestClient

from backend.main import create_app


def test_articles_list_empty(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SWIFT_ARTICLES_DIR", str(tmp_path))
    from backend.config import get_settings

    get_settings.cache_clear()

    client = TestClient(create_app())
    res = client.get("/api/articles")
    assert res.status_code == 200
    assert res.json() == {"articles": []}


def test_articles_download_rejects_traversal(monkeypatch):
    client = TestClient(create_app())
    res = client.get("/api/articles/../secret.txt")
    assert res.status_code in (400, 404)

