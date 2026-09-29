"""App-level tests without network: auth guard, headers, static page."""
import importlib

from fastapi.testclient import TestClient


def _client(monkeypatch, user="", pw=""):
    monkeypatch.setenv("DASHBOARD_USERNAME", user)
    monkeypatch.setenv("DASHBOARD_PASSWORD", pw)
    import app.config as cfg
    importlib.reload(cfg)
    import app.main as main
    importlib.reload(main)
    return TestClient(main.app)


def test_public_mode_serves_index_with_security_headers(monkeypatch):
    c = _client(monkeypatch)
    r = c.get("/healthz")
    assert r.status_code == 200 and r.json()["ok"]
    r = c.get("/api/meta")
    assert r.json()["auth"] is False
    r = c.get("/")
    assert r.status_code == 200 and "TTF Gas Desk" in r.text
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]


def test_basic_auth_guard(monkeypatch):
    c = _client(monkeypatch, "trader", "s3cret")
    assert c.get("/").status_code == 401
    assert c.get("/healthz").status_code == 200
    assert c.get("/", auth=("trader", "wrong")).status_code == 401
    assert c.get("/api/meta", auth=("trader", "s3cret")).json()["auth"] is True
