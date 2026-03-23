"""Tests for pleng CLI tool."""
import os
import json
from unittest.mock import patch, MagicMock

import pytest

os.environ["PLATFORM_API_URL"] = "http://mock-api:8000"
os.environ["PLENG_API_KEY"] = "pleng_testkey123"

import pleng


class TestHeaders:
    def test_includes_api_key(self):
        h = pleng._headers()
        assert h["X-API-Key"] == "pleng_testkey123"
        assert h["Content-Type"] == "application/json"

    def test_no_key_if_empty(self):
        original = pleng.API_KEY
        pleng.API_KEY = ""
        h = pleng._headers()
        assert "X-API-Key" not in h
        pleng.API_KEY = original


class TestGetPost:
    @patch("pleng.requests")
    def test_get_success(self, mock_req):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [{"name": "app1"}]
        mock_req.get.return_value = mock_resp

        result = pleng._get("/api/sites")
        assert result == [{"name": "app1"}]

    @patch("pleng.requests")
    def test_get_connection_error(self, mock_req):
        import requests
        mock_req.get.side_effect = requests.ConnectionError()
        mock_req.ConnectionError = requests.ConnectionError

        result = pleng._get("/api/sites")
        assert "error" in result
        assert "Cannot connect" in result["error"]

    @patch("pleng.requests")
    def test_get_http_error(self, mock_req):
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.json.return_value = {"detail": "Internal error"}
        mock_req.get.return_value = mock_resp

        result = pleng._get("/api/sites")
        assert "detail" in result

    @patch("pleng.requests")
    def test_post_success(self, mock_req):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"site_id": "123", "status": "staging"}
        mock_req.post.return_value = mock_resp

        result = pleng._post("/api/deploy/compose", {"name": "test"})
        assert result["status"] == "staging"


class TestCommandParsing:
    def test_flag_extraction(self):
        assert pleng._flag(["--name", "myapp"], "--name") == "myapp"
        assert pleng._flag(["--name", "myapp"], "--other") is None
        assert pleng._flag([], "--name") is None

    @patch("pleng._get")
    def test_cmd_sites_empty(self, mock_get, capsys):
        mock_get.return_value = []
        pleng.cmd_sites()
        assert "No sites" in capsys.readouterr().out

    @patch("pleng._get")
    def test_cmd_sites_with_data(self, mock_get, capsys):
        mock_get.return_value = [
            {"name": "app1", "status": "staging", "production_domain": None, "staging_domain": "test.sslip.io"}
        ]
        pleng.cmd_sites()
        out = capsys.readouterr().out
        assert "app1" in out
        assert "staging" in out

    @patch("pleng._post")
    def test_cmd_stop(self, mock_post, capsys):
        mock_post.return_value = {"ok": True}
        pleng.cmd_stop(["myapp"])
        assert "Stopped" in capsys.readouterr().out

    @patch("pleng._post")
    def test_cmd_remove_staging(self, mock_post, capsys):
        mock_post.return_value = {"ok": True, "kept_files": False}
        pleng.cmd_remove(["myapp"])
        assert "staging" in capsys.readouterr().out

    @patch("pleng._post")
    def test_cmd_remove_production(self, mock_post, capsys):
        mock_post.return_value = {"ok": True, "kept_files": True}
        pleng.cmd_remove(["prod-app"])
        assert "production" in capsys.readouterr().out

    def test_cmd_destroy_no_confirm(self, capsys):
        with pytest.raises(SystemExit):
            pleng.cmd_destroy(["myapp"])

    @patch("pleng._post")
    def test_cmd_destroy_with_confirm(self, mock_post, capsys):
        mock_post.return_value = {"ok": True}
        pleng.cmd_destroy(["myapp", "--confirm", "yes"])
        assert "Destroyed" in capsys.readouterr().out
