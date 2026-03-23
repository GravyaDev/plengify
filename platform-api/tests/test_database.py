"""Tests for database.py — SQLite operations."""
import os
import tempfile

import pytest

# Use temp DB for tests
os.environ["DATABASE_PATH"] = tempfile.mktemp(suffix=".db")

import database as db


@pytest.fixture(autouse=True)
def init_db():
    db.init()
    yield
    # Clean up
    with db._conn() as c:
        c.execute("DELETE FROM site_logs")
        c.execute("DELETE FROM sites")
        c.execute("DELETE FROM settings")
        c.execute("DELETE FROM traffic")


class TestSites:
    def test_create_site(self):
        site = db.create_site("test-app", deploy_mode="compose")
        assert site["name"] == "test-app"
        assert site["status"] == "deploying"
        assert site["id"]

    def test_get_site_by_id(self):
        site = db.create_site("app1")
        fetched = db.get_site(site["id"])
        assert fetched["name"] == "app1"

    def test_get_site_by_name(self):
        db.create_site("my-app")
        fetched = db.get_site_by_name("my-app")
        assert fetched is not None
        assert fetched["name"] == "my-app"

    def test_get_site_not_found(self):
        assert db.get_site("nonexistent") is None
        assert db.get_site_by_name("nonexistent") is None

    def test_get_all_sites(self):
        db.create_site("app1")
        db.create_site("app2")
        sites = db.get_all_sites()
        assert len(sites) == 2

    def test_update_site(self):
        site = db.create_site("app1")
        db.update_site(site["id"], status="staging", staging_domain="test.sslip.io")
        updated = db.get_site(site["id"])
        assert updated["status"] == "staging"
        assert updated["staging_domain"] == "test.sslip.io"

    def test_delete_site(self):
        site = db.create_site("app1")
        db.delete_site(site["id"])
        assert db.get_site(site["id"]) is None

    def test_duplicate_name_raises(self):
        db.create_site("app1")
        with pytest.raises(Exception):
            db.create_site("app1")


class TestSiteLogs:
    def test_add_and_get_logs(self):
        site = db.create_site("app1")
        db.add_site_log(site["id"], "Starting deploy")
        db.add_site_log(site["id"], "Deploy failed", level="error")
        logs = db.get_site_logs(site["id"])
        assert len(logs) == 2
        assert logs[0]["level"] == "error"  # Most recent first
        assert logs[1]["message"] == "Starting deploy"


class TestHealthMonitoring:
    def test_increment_failures(self):
        site = db.create_site("app1")
        assert db.get_failures(site["id"]) == 0
        db.increment_failures(site["id"])
        assert db.get_failures(site["id"]) == 1
        db.increment_failures(site["id"])
        db.increment_failures(site["id"])
        assert db.get_failures(site["id"]) == 3

    def test_reset_failures(self):
        site = db.create_site("app1")
        db.increment_failures(site["id"])
        db.increment_failures(site["id"])
        db.reset_failures(site["id"])
        assert db.get_failures(site["id"]) == 0


class TestSettings:
    def test_get_set_setting(self):
        db.set_setting("test_key", "test_value")
        assert db.get_setting("test_key") == "test_value"

    def test_get_missing_setting(self):
        assert db.get_setting("nonexistent") is None

    def test_api_key_generation(self):
        key = db.get_or_create_api_key()
        assert key.startswith("pleng_")
        # Same key on second call
        assert db.get_or_create_api_key() == key
