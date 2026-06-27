# tests/upcloud/test_errors.py

"""Tests for UpCloud API error handling and retry/backoff."""

import pytest
import requests
from upcloud_api import UpCloudAPIError, UpCloudClientError

from ots_shared.upcloud.errors import api_errors, is_retryable, with_backoff


class TestAPIError:
    def test_generic_api_error_exit_1(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            with api_errors():
                raise UpCloudAPIError("INVALID_REQUEST", "bad input")
        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "UpCloud API error (INVALID_REQUEST): bad input" in err

    def test_auth_failed_exit_2(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            with api_errors():
                raise UpCloudAPIError("AUTHENTICATION_FAILED", "bad token")
        assert exc_info.value.code == 2
        assert "auth failed" in capsys.readouterr().err

    def test_authorization_failed_exit_2(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            with api_errors():
                raise UpCloudAPIError("AUTHORIZATION_FAILED", "nope")
        assert exc_info.value.code == 2
        assert "auth failed" in capsys.readouterr().err

    def test_user_data_invalid_surfaced_verbatim(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            with api_errors():
                raise UpCloudAPIError("USER_DATA_INVALID", "payload rejected")
        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "USER_DATA_INVALID" in err
        assert "payload rejected" in err


class TestClientError:
    def test_client_error_exit_2(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            with api_errors():
                raise UpCloudClientError("credentials not defined")
        assert exc_info.value.code == 2
        assert "client error" in capsys.readouterr().err


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

    def test_requests_timeout_propagates_to_os_error_branch(self, capsys):
        # requests.Timeout subclasses OSError and is NOT wrapped by the SDK.
        with pytest.raises(SystemExit) as exc_info:
            with api_errors():
                raise requests.exceptions.Timeout("read timed out")
        assert exc_info.value.code == 1
        assert "OS error:" in capsys.readouterr().err


class TestPassthrough:
    def test_reraises_unknown(self):
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
        called = []
        with api_errors():
            called.append("ran")
        assert called == ["ran"]


class TestIsRetryable:
    def test_connection_error_retryable(self):
        assert is_retryable(ConnectionError("x")) is True

    def test_timeout_error_retryable(self):
        assert is_retryable(TimeoutError("x")) is True

    def test_requests_timeout_retryable(self):
        assert is_retryable(requests.exceptions.Timeout("x")) is True

    def test_rate_limit_code_retryable(self):
        assert is_retryable(UpCloudAPIError("TOO_MANY_REQUESTS", "slow down")) is True

    def test_5xx_message_retryable(self):
        assert is_retryable(UpCloudAPIError("INTERNAL_ERROR", "service unavailable")) is True

    def test_4xx_validation_not_retryable(self):
        assert is_retryable(UpCloudAPIError("INVALID_REQUEST", "bad field")) is False

    def test_not_found_not_retryable(self):
        assert is_retryable(UpCloudAPIError("SERVER_NOT_FOUND", "gone")) is False

    def test_value_error_not_retryable(self):
        assert is_retryable(ValueError("x")) is False


class TestWithBackoff:
    def test_returns_on_first_success(self):
        calls = []
        result = with_backoff(lambda: calls.append(1) or "ok", sleep=lambda _: None)
        assert result == "ok"
        assert len(calls) == 1

    def test_retries_then_succeeds(self):
        attempts = {"n": 0}
        sleeps = []

        def flaky():
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise UpCloudAPIError("TOO_MANY_REQUESTS", "slow down")
            return "done"

        result = with_backoff(flaky, attempts=4, sleep=sleeps.append)
        assert result == "done"
        assert attempts["n"] == 3
        assert len(sleeps) == 2  # two retries -> two sleeps

    def test_exhausts_budget_and_reraises(self):
        def always_429():
            raise UpCloudAPIError("TOO_MANY_REQUESTS", "slow down")

        with pytest.raises(UpCloudAPIError):
            with_backoff(always_429, attempts=3, sleep=lambda _: None)

    def test_non_retryable_propagates_immediately(self):
        calls = {"n": 0}

        def fails():
            calls["n"] += 1
            raise UpCloudAPIError("INVALID_REQUEST", "bad")

        with pytest.raises(UpCloudAPIError):
            with_backoff(fails, attempts=5, sleep=lambda _: None)
        assert calls["n"] == 1  # no retries on a non-transient error

    def test_backoff_is_bounded_by_max_delay(self):
        sleeps = []

        def always_timeout():
            raise TimeoutError("x")

        with pytest.raises(TimeoutError):
            with_backoff(
                always_timeout,
                attempts=6,
                base_delay=10.0,
                max_delay=15.0,
                sleep=sleeps.append,
            )
        assert all(s <= 15.0 for s in sleeps)

    def test_zero_attempts_rejected(self):
        with pytest.raises(ValueError, match="attempts"):
            with_backoff(lambda: "x", attempts=0)
