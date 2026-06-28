# tests/digitalocean/test_config.py

"""Tests for ots_shared.digitalocean.config."""

import sys
from unittest.mock import MagicMock, patch

import pytest

from ots_shared.digitalocean.config import Config


class TestConfigEnvVars:
    def test_reads_token_env(self, monkeypatch):
        monkeypatch.setenv("DIGITALOCEAN_TOKEN", "dop_v1_abc")
        monkeypatch.delenv("DIGITALOCEAN_ACCESS_TOKEN", raising=False)
        assert Config().token == "dop_v1_abc"

    def test_reads_access_token_alias(self, monkeypatch):
        monkeypatch.delenv("DIGITALOCEAN_TOKEN", raising=False)
        monkeypatch.setenv("DIGITALOCEAN_ACCESS_TOKEN", "dop_alias")
        assert Config().token == "dop_alias"

    def test_token_wins_over_alias(self, monkeypatch):
        monkeypatch.setenv("DIGITALOCEAN_TOKEN", "primary")
        monkeypatch.setenv("DIGITALOCEAN_ACCESS_TOKEN", "alias")
        assert Config().token == "primary"

    def test_defaults_to_empty_string(self, monkeypatch):
        monkeypatch.delenv("DIGITALOCEAN_TOKEN", raising=False)
        monkeypatch.delenv("DIGITALOCEAN_ACCESS_TOKEN", raising=False)
        assert Config().token == ""

    def test_explicit_kwargs_override_env(self, monkeypatch):
        monkeypatch.setenv("DIGITALOCEAN_TOKEN", "from-env")
        assert Config(token="from-arg").token == "from-arg"


class TestConfigGetClient:
    def test_token_path_builds_client(self):
        cfg = Config(token="dop_v1_abc")
        fake_pydo = MagicMock()
        with patch.dict(sys.modules, {"pydo": fake_pydo}):
            client = cfg.get_client()
            fake_pydo.Client.assert_called_once()
            kwargs = fake_pydo.Client.call_args.kwargs
            assert kwargs["token"] == "dop_v1_abc"
            assert kwargs["timeout"] == 120
            assert client is fake_pydo.Client.return_value

    def test_custom_timeout_forwarded(self):
        cfg = Config(token="dop_x", timeout=45)
        fake_pydo = MagicMock()
        with patch.dict(sys.modules, {"pydo": fake_pydo}):
            cfg.get_client()
            assert fake_pydo.Client.call_args.kwargs["timeout"] == 45

    def test_no_token_fail_loud(self, monkeypatch):
        monkeypatch.delenv("DIGITALOCEAN_TOKEN", raising=False)
        monkeypatch.delenv("DIGITALOCEAN_ACCESS_TOKEN", raising=False)
        cfg = Config(token="")
        fake_pydo = MagicMock()
        with patch.dict(sys.modules, {"pydo": fake_pydo}):
            with pytest.raises(SystemExit, match="DIGITALOCEAN_TOKEN"):
                cfg.get_client()
        fake_pydo.Client.assert_not_called()

    def test_missing_pydo_fail_loud_with_install_hint(self):
        cfg = Config(token="dop_x")
        # Simulate the extra not being installed: importing pydo raises.
        with patch.dict(sys.modules, {"pydo": None}):
            with pytest.raises(SystemExit, match="pip install 'ots-shared\\[digitalocean\\]'"):
                cfg.get_client()

    def test_application_version_is_str(self):
        assert isinstance(Config().application_version, str)
