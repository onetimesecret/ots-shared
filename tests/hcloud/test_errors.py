# tests/hcloud/test_errors.py

"""Tests for hcloud API error handling."""

from unittest.mock import MagicMock

import pytest
from hcloud import APIException, HCloudException
from hcloud.actions.domain import ActionException

from ots_shared.hcloud.errors import api_errors


class TestAPIException:
    def test_catches_api_exception(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            with api_errors():
                raise APIException(code=403, message="Forbidden", details=None)
        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "Hetzner API error (403): Forbidden" in err

    def test_catches_api_exception_string_code(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            with api_errors():
                raise APIException(code="rate_limit", message="Too many requests", details=None)
        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "rate_limit" in err
        assert "Too many requests" in err


class TestHCloudException:
    def test_catches_generic_hcloud_exception(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            with api_errors():
                raise HCloudException("something went wrong")
        assert exc_info.value.code == 1
        assert "Hetzner API error:" in capsys.readouterr().err

    def test_catches_action_exception(self, capsys):
        action = MagicMock()
        action.error = {"code": "server_error", "message": "Action failed"}
        action.id = 99
        with pytest.raises(SystemExit) as exc_info:
            with api_errors():
                raise ActionException(action=action)
        assert exc_info.value.code == 1
        assert "Hetzner API error:" in capsys.readouterr().err


class TestNetworkErrors:
    def test_catches_connection_error(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            with api_errors():
                raise ConnectionError("Connection refused")
        assert exc_info.value.code == 1
        assert "Network error:" in capsys.readouterr().err

    def test_catches_timeout_error(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            with api_errors():
                raise TimeoutError("timed out")
        assert exc_info.value.code == 1
        assert "Network error:" in capsys.readouterr().err

    def test_catches_os_error(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            with api_errors():
                raise OSError("No route to host")
        assert exc_info.value.code == 1
        assert "OS error:" in capsys.readouterr().err


class TestPassthrough:
    def test_reraises_unknown_exceptions(self):
        with pytest.raises(ValueError, match="unrelated"):
            with api_errors():
                raise ValueError("unrelated")

    def test_passes_system_exit_through(self):
        with pytest.raises(SystemExit) as exc_info:
            with api_errors():
                raise SystemExit("missing token")
        assert exc_info.value.code == "missing token"

    def test_keyboard_interrupt_exits_130(self):
        with pytest.raises(SystemExit) as exc_info:
            with api_errors():
                raise KeyboardInterrupt
        assert exc_info.value.code == 130


class TestSuccessPath:
    def test_no_exception_yields_normally(self):
        # Sanity: api_errors() must not interfere with the happy path.
        # If a future refactor adds a finally/return path it would be easy
        # to accidentally swallow the yielded value.
        called = []
        with api_errors():
            called.append("ran")
        assert called == ["ran"]
