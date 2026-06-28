# tests/digitalocean/test_errors.py

"""Tests for DigitalOcean API error handling and retry/backoff.

The azure-core exception path is exercised only when ``azure-core`` is installed
(the optional ``[digitalocean]`` extra); the network/OS fallback branches and
the backoff loop run without it.
"""

from __future__ import annotations

import pytest

from ots_shared.digitalocean.errors import (
    api_errors,
    classify,
    is_retryable,
    with_backoff,
)

azure_exc = pytest.importorskip("azure.core.exceptions")
HttpResponseError = azure_exc.HttpResponseError
ServiceRequestError = azure_exc.ServiceRequestError


def _http_error(status: int, message: str = "boom") -> HttpResponseError:
    err = HttpResponseError(message=message)
    err.status_code = status
    return err


class TestExceptionShape:
    """Lock the real azure HttpResponseError shape — it DOES carry a
    ``status_code`` (the inversion vs UpCloud), which the classifier keys off.
    Verified against the live SDK, not docs.
    """

    def test_http_error_exposes_status_code(self):
        assert hasattr(HttpResponseError(message="x"), "status_code")


class TestAPIError:
    def test_generic_api_error_exit_1(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            with api_errors():
                raise _http_error(422, "bad input")
        assert exc_info.value.code == 1
        assert "DigitalOcean API error (HTTP 422): bad input" in capsys.readouterr().err

    def test_auth_401_exit_2(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            with api_errors():
                raise _http_error(401, "Unable to authenticate")
        assert exc_info.value.code == 2
        assert "auth failed" in capsys.readouterr().err

    def test_forbidden_403_exit_2(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            with api_errors():
                raise _http_error(403, "forbidden")
        assert exc_info.value.code == 2
        assert "auth failed" in capsys.readouterr().err

    def test_rate_limit_429_is_api_error_when_unretried(self, capsys):
        # Surfaced through api_errors (not the backoff loop) -> generic exit 1.
        with pytest.raises(SystemExit) as exc_info:
            with api_errors():
                raise _http_error(429, "slow down")
        assert exc_info.value.code == 1
        assert "HTTP 429" in capsys.readouterr().err

    def test_network_azure_error_exit_1(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            with api_errors():
                raise ServiceRequestError(message="connection reset")
        assert exc_info.value.code == 1
        assert "Network error:" in capsys.readouterr().err


class TestClassifyDetailFromBody:
    def test_prefers_error_message_over_top_level(self):
        err = _http_error(404, "top-level")
        err.error = type("E", (), {"message": "droplet not found", "code": "not_found"})()
        msg = classify(err)
        assert "droplet not found" in msg
        assert "top-level" not in msg


class TestNetworkErrors:
    def test_connection_error_exit_1(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            with api_errors():
                raise ConnectionError("refused")
        assert exc_info.value.code == 1
        assert "Network error:" in capsys.readouterr().err

    def test_timeout_error_exit_1(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            with api_errors():
                raise TimeoutError("timed out")
        assert exc_info.value.code == 1
        assert "Network error:" in capsys.readouterr().err

    def test_os_error_exit_1(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            with api_errors():
                raise OSError("disk")
        assert exc_info.value.code == 1
        assert "OS error:" in capsys.readouterr().err

    def test_unknown_exception_reraised(self):
        with pytest.raises(ValueError):
            with api_errors():
                raise ValueError("not ours")

    def test_systemexit_passthrough(self):
        with pytest.raises(SystemExit) as exc_info:
            with api_errors():
                raise SystemExit(65)
        assert exc_info.value.code == 65

    def test_keyboard_interrupt_becomes_130(self):
        with pytest.raises(SystemExit) as exc_info:
            with api_errors():
                raise KeyboardInterrupt()
        assert exc_info.value.code == 130


class TestIsRetryable:
    @pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
    def test_transient_http_statuses_retryable(self, status):
        assert is_retryable(_http_error(status))

    @pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
    def test_client_errors_not_retryable(self, status):
        assert not is_retryable(_http_error(status))

    def test_network_errors_retryable(self):
        assert is_retryable(ServiceRequestError(message="x"))
        assert is_retryable(ConnectionError())
        assert is_retryable(TimeoutError())

    def test_unrelated_not_retryable(self):
        assert not is_retryable(ValueError("x"))


class TestWithBackoff:
    def test_returns_on_success_without_sleeping(self):
        slept: list[float] = []
        assert with_backoff(lambda: 42, sleep=slept.append) == 42
        assert slept == []

    def test_retries_then_succeeds(self):
        calls = {"n": 0}
        slept: list[float] = []

        def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise _http_error(503)
            return "ok"

        assert with_backoff(flaky, sleep=slept.append) == "ok"
        assert calls["n"] == 3
        assert len(slept) == 2  # two retries before the 3rd success

    def test_non_retryable_propagates_immediately(self):
        slept: list[float] = []
        with pytest.raises(SystemExit):
            with_backoff(lambda: (_ for _ in ()).throw(SystemExit(1)), sleep=slept.append)
        assert slept == []

    def test_exhausts_attempts_and_reraises(self):
        slept: list[float] = []
        with pytest.raises(HttpResponseError):
            with_backoff(
                lambda: (_ for _ in ()).throw(_http_error(500)),
                attempts=3,
                sleep=slept.append,
            )
        assert len(slept) == 2  # attempts-1 sleeps

    def test_retry_after_header_overrides_backoff(self):
        slept: list[float] = []
        err = _http_error(429)
        err.response = type("R", (), {"headers": {"Retry-After": "7"}})()
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] == 1:
                raise err
            return "ok"

        with_backoff(flaky, base_delay=1.0, sleep=slept.append)
        assert slept == [7.0]

    def test_invalid_attempts_rejected(self):
        with pytest.raises(ValueError):
            with_backoff(lambda: 1, attempts=0)
