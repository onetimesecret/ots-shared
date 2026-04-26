# tests/hcloud/test_config.py

"""Tests for ots_shared.hcloud.config."""

from unittest.mock import patch

import pytest

from ots_shared.hcloud.config import Config


class TestConfigEnvVars:
    def test_reads_env_vars(self, monkeypatch):
        monkeypatch.setenv("HCLOUD_TOKEN", "tok-abc123")
        monkeypatch.setenv("HCLOUD_PROJECT_ID", "proj-42")
        cfg = Config()
        assert cfg.token == "tok-abc123"
        assert cfg.project_id == "proj-42"

    def test_defaults_to_empty_strings(self, monkeypatch):
        monkeypatch.delenv("HCLOUD_TOKEN", raising=False)
        monkeypatch.delenv("HCLOUD_PROJECT_ID", raising=False)
        cfg = Config()
        assert cfg.token == ""
        assert cfg.project_id == ""

    def test_blank_token_env_var(self, monkeypatch):
        # An explicitly blank HCLOUD_TOKEN must be treated as "not set" —
        # never silently used as the bearer token. The current contract is
        # that the empty string survives field default but get_client()
        # rejects it (see TestConfigGetClient.test_blank_token_raises).
        monkeypatch.setenv("HCLOUD_TOKEN", "")
        cfg = Config()
        assert cfg.token == ""

    def test_explicit_kwargs_override_env(self, monkeypatch):
        # Explicit constructor args must win over env vars, otherwise
        # programmatic callers (tests, scripts) can't override per-call.
        monkeypatch.setenv("HCLOUD_TOKEN", "from-env")
        monkeypatch.setenv("HCLOUD_PROJECT_ID", "env-proj")
        cfg = Config(token="from-arg", project_id="arg-proj")
        assert cfg.token == "from-arg"
        assert cfg.project_id == "arg-proj"


class TestConfigGetClient:
    def test_no_token_raises_system_exit(self, monkeypatch):
        monkeypatch.delenv("HCLOUD_TOKEN", raising=False)
        cfg = Config(token="")
        with pytest.raises(SystemExit, match="HCLOUD_TOKEN"):
            cfg.get_client()

    def test_blank_token_raises_system_exit(self, monkeypatch):
        # Blank string is the failure mode that "fail loud" exists to catch:
        # an unset env var falls through field default to ""; if the Client
        # were constructed with that, every call would hit Hetzner with an
        # empty Authorization header and surface a confusing 401. We exit
        # with a typed message instead.
        monkeypatch.delenv("HCLOUD_TOKEN", raising=False)
        cfg = Config(token="", project_id="proj-42")
        with pytest.raises(SystemExit, match="HCLOUD_TOKEN"):
            cfg.get_client()

    def test_returns_hcloud_client(self, monkeypatch):
        monkeypatch.setenv("HCLOUD_TOKEN", "tok-abc123")
        cfg = Config(token="tok-abc123", project_id="proj-42")
        with patch("ots_shared.hcloud.config.Client") as mock_client_cls:
            client = cfg.get_client()
            mock_client_cls.assert_called_once()
            kwargs = mock_client_cls.call_args.kwargs
            assert kwargs["token"] == "tok-abc123"
            assert kwargs["application_name"] == "hcloud-cli-proj-42"
            # version comes from importlib.metadata; just assert it's a str
            assert isinstance(kwargs["application_version"], str)
            assert client is mock_client_cls.return_value

    def test_application_name_includes_empty_project_id(self, monkeypatch):
        # No HCLOUD_PROJECT_ID set still works — application name reflects
        # the empty project id rather than crashing. Useful for ad-hoc uses
        # outside a configured environment.
        monkeypatch.setenv("HCLOUD_TOKEN", "tok-only")
        monkeypatch.delenv("HCLOUD_PROJECT_ID", raising=False)
        cfg = Config()
        with patch("ots_shared.hcloud.config.Client") as mock_client_cls:
            cfg.get_client()
            kwargs = mock_client_cls.call_args.kwargs
            assert kwargs["application_name"] == "hcloud-cli-"
