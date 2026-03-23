"""Tests for platform API endpoints."""
import os
import tempfile

os.environ["DATABASE_PATH"] = tempfile.mktemp(suffix=".db")
os.environ["PROJECTS_DIR"] = tempfile.mkdtemp()
os.environ["PUBLIC_IP"] = "1.2.3.4"
os.environ["WEB_UI_PASSWORD"] = "testpass123secure"

import pytest
from fastapi.testclient import TestClient

import database as db

# Init DB before importing app (startup event may not fire in test)
db.init()

import app as app_module
from app import app

# Force startup: set the API key and password that the middleware checks
app_module._api_key = db.get_or_create_api_key()
app_module._dashboard_password = "testpass123secure"

client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_db():
    db.init()
    yield
    with db._conn() as c:
        c.execute("DELETE FROM site_logs")
        c.execute("DELETE FROM sites")
        c.execute("DELETE FROM traffic")
        # Don't delete settings — API key must persist


def _api_key() -> str:
    return db.get_or_create_api_key()


def _auth_headers() -> dict:
    return {"X-API-Key": _api_key(), "X-Forwarded-For": "1.2.3.4"}


class TestHealth:
    def test_health_no_auth(self):
        r = client.get("/api/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_setup_status_no_auth(self):
        r = client.get("/api/setup-status")
        assert r.status_code == 200
        assert r.json()["public_ip"] == "1.2.3.4"


class TestAuth:
    def test_login_correct(self):
        r = client.post("/api/auth/login", json={"password": "testpass123secure"})
        assert r.status_code == 200
        assert r.json()["api_key"].startswith("pleng_")

    def test_login_wrong(self):
        r = client.post("/api/auth/login", json={"password": "wrong"})
        assert r.status_code == 401

    def test_sites_no_auth_external(self):
        """External request (with X-Forwarded-For) without key should be rejected."""
        r = client.get("/api/sites", headers={"X-Forwarded-For": "1.2.3.4"})
        assert r.status_code == 401

    def test_sites_with_auth(self):
        r = client.get("/api/sites", headers={**_auth_headers(), "X-Forwarded-For": "1.2.3.4"})
        assert r.status_code == 200
        assert r.json() == []


class TestSkillMd:
    def test_skill_md_served(self):
        r = client.get("/skill.md")
        assert r.status_code == 200
        assert "Pleng" in r.text
        assert "1.2.3.4" in r.text
        assert "X-API-Key" in r.text


class TestSites:
    def test_list_empty(self):
        r = client.get("/api/sites", headers=_auth_headers())
        assert r.json() == []

    def test_get_not_found(self):
        r = client.get("/api/sites/nonexistent", headers=_auth_headers())
        assert r.status_code == 404

    def test_stop_not_found(self):
        r = client.post("/api/sites/nonexistent/stop", headers=_auth_headers())
        assert r.status_code == 404

    def test_deploy_compose_missing_path(self):
        r = client.post("/api/deploy/compose",
                        json={"name": "test", "compose_path": "/nonexistent"},
                        headers=_auth_headers())
        assert r.status_code == 400

    def test_deploy_git_missing_fields(self):
        r = client.post("/api/deploy/git",
                        json={},
                        headers=_auth_headers())
        assert r.status_code == 422  # FastAPI validation — missing required fields

    def test_deploy_duplicate_name(self):
        db.create_site("existing-app")
        r = client.post("/api/deploy/compose",
                        json={"name": "existing-app", "compose_path": "/tmp"},
                        headers=_auth_headers())
        assert r.status_code == 400
        assert "already exists" in r.json()["detail"]


class TestAnalytics:
    def test_analytics_no_site(self):
        r = client.get("/api/sites/nonexistent/analytics", headers=_auth_headers())
        assert r.status_code == 404

    def test_analytics_no_data(self):
        site = db.create_site("test-app")
        db.update_site(site["id"], staging_domain="test.sslip.io")
        r = client.get(f"/api/sites/{site['id']}/analytics", headers=_auth_headers())
        assert r.status_code == 200
        data = r.json()
        assert data["stats"]["pageviews"] == 0
