"""Tests for deployer.py — deploy logic (without actual Docker)."""
import os
import tempfile

os.environ["DATABASE_PATH"] = tempfile.mktemp(suffix=".db")
os.environ["PROJECTS_DIR"] = tempfile.mkdtemp()
os.environ["PUBLIC_IP"] = "1.2.3.4"

import pytest
import yaml

import database as db
import deployer


@pytest.fixture(autouse=True)
def setup():
    db.init()
    yield
    with db._conn() as c:
        c.execute("DELETE FROM site_logs")
        c.execute("DELETE FROM sites")


class TestStagingDomain:
    def test_generates_domain(self):
        domain = deployer.staging_domain("my-app")
        assert "1.2.3.4.sslip.io" in domain
        assert len(domain.split(".")[0]) == 4  # 4-char hash

    def test_deterministic(self):
        assert deployer.staging_domain("app") == deployer.staging_domain("app")

    def test_different_per_name(self):
        assert deployer.staging_domain("app1") != deployer.staging_domain("app2")


class TestResolveWorkspace:
    def test_by_project_path(self):
        d = tempfile.mkdtemp()
        site = {"id": "123", "name": "test", "project_path": d}
        assert deployer._resolve_workspace(site) == d

    def test_by_name(self):
        d = os.path.join(deployer.PROJECTS_DIR, "my-app")
        os.makedirs(d, exist_ok=True)
        site = {"id": "123", "name": "my-app", "project_path": ""}
        assert deployer._resolve_workspace(site) == d

    def test_fallback(self):
        site = {"id": "123", "name": "nonexistent", "project_path": ""}
        result = deployer._resolve_workspace(site)
        assert "nonexistent" in result


class TestGeneratePlengOverride:
    def test_generates_override(self):
        workspace = tempfile.mkdtemp()
        compose = {"services": {"web": {"build": ".", "ports": ["80:3000"]}}}
        with open(os.path.join(workspace, "docker-compose.yml"), "w") as f:
            yaml.dump(compose, f)

        deployer._generate_pleng_override(workspace, "test-app", "abcd.1.2.3.4.sslip.io")

        override_path = os.path.join(workspace, "docker-compose.pleng.yml")
        assert os.path.exists(override_path)

        with open(override_path) as f:
            override = yaml.safe_load(f)

        web = override["services"]["web"]
        # Ports removed
        assert "ports" not in web
        # Traefik labels added
        assert any("traefik.enable=true" in l for l in web["labels"])
        assert any("abcd.1.2.3.4.sslip.io" in l for l in web["labels"])
        # Port detected from original compose
        assert any("3000" in l for l in web["labels"])
        # Build context absolute
        assert web["build"] == workspace
        # Network added
        assert "pleng_web" in web.get("networks", [])

    def test_with_production_domain(self):
        workspace = tempfile.mkdtemp()
        compose = {"services": {"web": {"build": ".", "ports": ["80:8080"]}}}
        with open(os.path.join(workspace, "docker-compose.yml"), "w") as f:
            yaml.dump(compose, f)

        deployer._generate_pleng_override(workspace, "app", "staging.sslip.io",
                                           production_domain="app.example.com")

        with open(os.path.join(workspace, "docker-compose.pleng.yml")) as f:
            override = yaml.safe_load(f)

        labels = override["services"]["web"]["labels"]
        assert any("app.example.com" in l for l in labels)
        assert any("letsencrypt" in l for l in labels)
        assert any("websecure" in l for l in labels)

    def test_user_compose_untouched(self):
        workspace = tempfile.mkdtemp()
        original = "services:\n  web:\n    build: .\n    ports:\n      - '80:3000'\n"
        with open(os.path.join(workspace, "docker-compose.yml"), "w") as f:
            f.write(original)

        deployer._generate_pleng_override(workspace, "test", "x.sslip.io")

        with open(os.path.join(workspace, "docker-compose.yml")) as f:
            assert f.read() == original  # Not modified

    def test_multi_service_compose(self):
        workspace = tempfile.mkdtemp()
        compose = {
            "services": {
                "web": {"build": ".", "ports": ["80:3000"]},
                "db": {"image": "postgres:16"},
            }
        }
        with open(os.path.join(workspace, "docker-compose.yml"), "w") as f:
            yaml.dump(compose, f)

        deployer._generate_pleng_override(workspace, "app", "x.sslip.io")

        with open(os.path.join(workspace, "docker-compose.pleng.yml")) as f:
            override = yaml.safe_load(f)

        # Web gets labels, db stays untouched
        assert "labels" in override["services"]["web"]
        assert "labels" not in override["services"]["db"]


class TestComposeCmd:
    def test_uses_pleng_compose(self):
        workspace = tempfile.mkdtemp()
        # Create pleng compose
        with open(os.path.join(workspace, "docker-compose.pleng.yml"), "w") as f:
            f.write("services: {}")

        cmd = deployer._compose_cmd("pleng-test", workspace, "up", "-d")
        assert "docker-compose.pleng.yml" in cmd[3]
        assert "pleng-test" in cmd

    def test_falls_back_to_user_compose(self):
        workspace = tempfile.mkdtemp()
        with open(os.path.join(workspace, "docker-compose.yml"), "w") as f:
            f.write("services: {}")

        cmd = deployer._compose_cmd("pleng-test", workspace, "ps")
        assert "docker-compose.yml" in cmd[3]
