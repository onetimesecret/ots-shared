# tests/upcloud/test_config.py

"""Tests for ots_shared.upcloud.config."""

from unittest.mock import patch

import pytest

from ots_shared.upcloud.config import Config


class TestConfigEnvVars:
    def test_reads_token_env(self, monkeypatch):
        monkeypatch.setenv("UPCLOUD_TOKEN", "ucat_abc123")
        monkeypatch.delenv("UPCLOUD_USERNAME", raising=False)
        monkeypatch.delenv("UPCLOUD_PASSWORD", raising=False)
        cfg = Config()
        assert cfg.token == "ucat_abc123"

    def test_reads_basic_env(self, monkeypatch):
        monkeypatch.delenv("UPCLOUD_TOKEN", raising=False)
        monkeypatch.setenv("UPCLOUD_USERNAME", "apiuser")
        monkeypatch.setenv("UPCLOUD_PASSWORD", "secret")
        cfg = Config()
        assert cfg.username == "apiuser"
        assert cfg.password == "secret"

    def test_defaults_to_empty_strings(self, monkeypatch):
        for var in ("UPCLOUD_TOKEN", "UPCLOUD_USERNAME", "UPCLOUD_PASSWORD"):
            monkeypatch.delenv(var, raising=False)
        cfg = Config()
        assert cfg.token == ""
        assert cfg.username == ""
        assert cfg.password == ""

    def test_explicit_kwargs_override_env(self, monkeypatch):
        monkeypatch.setenv("UPCLOUD_TOKEN", "from-env")
        cfg = Config(token="from-arg")
        assert cfg.token == "from-arg"


class TestConfigGetClient:
    def test_token_path_builds_bearer_client(self, monkeypatch):
        monkeypatch.delenv("UPCLOUD_USERNAME", raising=False)
        monkeypatch.delenv("UPCLOUD_PASSWORD", raising=False)
        cfg = Config(token="ucat_abc123")
        with patch("upcloud_api.CloudManager") as mock_mgr:
            client = cfg.get_client()
            mock_mgr.assert_called_once()
            kwargs = mock_mgr.call_args.kwargs
            assert kwargs["token"] == "ucat_abc123"
            assert "username" not in kwargs
            assert kwargs["timeout"] == 60
            assert client is mock_mgr.return_value

    def test_token_wins_over_basic(self, monkeypatch):
        # Both present -> token path only (mirrors SDK precedence).
        cfg = Config(token="ucat_x", username="u", password="p")
        with patch("upcloud_api.CloudManager") as mock_mgr:
            cfg.get_client()
            kwargs = mock_mgr.call_args.kwargs
            assert kwargs["token"] == "ucat_x"
            assert "username" not in kwargs
            assert "password" not in kwargs

    def test_basic_path_builds_basic_client(self):
        cfg = Config(token="", username="apiuser", password="secret")
        with patch("upcloud_api.CloudManager") as mock_mgr:
            cfg.get_client()
            kwargs = mock_mgr.call_args.kwargs
            assert kwargs["username"] == "apiuser"
            assert kwargs["password"] == "secret"
            assert "token" not in kwargs

    def test_custom_timeout_forwarded(self):
        cfg = Config(token="ucat_x", timeout=120)
        with patch("upcloud_api.CloudManager") as mock_mgr:
            cfg.get_client()
            assert mock_mgr.call_args.kwargs["timeout"] == 120

    def test_no_creds_fail_loud(self, monkeypatch):
        for var in ("UPCLOUD_TOKEN", "UPCLOUD_USERNAME", "UPCLOUD_PASSWORD"):
            monkeypatch.delenv(var, raising=False)
        cfg = Config(token="", username="", password="")
        with pytest.raises(SystemExit, match="UPCLOUD_TOKEN"):
            cfg.get_client()

    def test_username_without_password_fail_loud(self):
        # A half-configured Basic path must fail loud, not silently fall
        # back to a missing-credential client.
        cfg = Config(token="", username="apiuser", password="")
        with pytest.raises(SystemExit, match="UPCLOUD_TOKEN"):
            cfg.get_client()

    def test_password_without_username_fail_loud(self):
        cfg = Config(token="", username="", password="secret")
        with pytest.raises(SystemExit, match="UPCLOUD_TOKEN"):
            cfg.get_client()
