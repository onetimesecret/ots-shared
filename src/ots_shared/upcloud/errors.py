# src/ots_shared/upcloud/errors.py

"""Friendly error handling and retry/backoff for UpCloud API calls.

UpCloud's exceptions are leaner than Hetzner's:

  * ``UpCloudAPIError`` (HTTP >= 400) exposes **only** ``.error_code`` and
    ``.error_message`` — there is **no** ``.status_code``. Status-based
    branching (429 vs 404) must infer from ``error_code`` / message text.
  * ``UpCloudClientError`` (its base) is raised for client-side problems
    (missing/undefined credentials, keyring failures).
  * Raw ``requests`` timeouts / connection errors propagate **unwrapped**
    (they are not converted into either UpCloud class).

The SDK has **no** retry or backoff of its own, so :func:`with_backoff`
provides a small, defensive exponential backoff for transient 429 / 5xx
responses and raw network timeouts.
"""

from __future__ import annotations

import contextlib
import sys
import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")

# Auth failures are config/credential problems (exit 2); everything else
# the API rejects is a runtime API error (exit 1), mirroring the lots
# exit-code conventions (0 ok, 1 API, 2 config, 65 validation, 70 drift).
_AUTH_CODES = frozenset({"AUTHENTICATION_FAILED", "AUTHORIZATION_FAILED"})


def _upcloud_exc_classes() -> tuple[type | None, type | None]:
    """Return (UpCloudAPIError, UpCloudClientError) or (None, None) if absent.

    Guarded so a non-UpCloud install never hard-imports the dependency.
    """
    try:
        from upcloud_api import UpCloudAPIError, UpCloudClientError
    except ImportError:  # pragma: no cover - upcloud-api not installed
        return None, None
    return UpCloudAPIError, UpCloudClientError


@contextlib.contextmanager
def api_errors():
    """Catch UpCloud API errors and exit with a user-friendly message.

    Maps ``UpCloudAPIError`` / ``UpCloudClientError`` and raw network
    errors to friendly stderr lines and a deterministic exit code.
    ``SystemExit`` is re-raised untouched; ``KeyboardInterrupt`` becomes
    ``SystemExit(130)``; unknown exceptions are re-raised as-is.
    """
    try:
        yield
    except SystemExit:
        raise
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as exc:
        _handle(exc)


def _handle(exc: BaseException):
    """Map known exceptions to friendly messages, re-raise the rest."""
    api_error_cls, client_error_cls = _upcloud_exc_classes()

    if api_error_cls is not None and isinstance(exc, api_error_cls):
        code = getattr(exc, "error_code", "") or ""
        message = getattr(exc, "error_message", "") or ""
        if code in _AUTH_CODES:
            print(
                "UpCloud auth failed; check UPCLOUD_TOKEN / "
                "UPCLOUD_USERNAME+UPCLOUD_PASSWORD "
                f"({code}: {message})",
                file=sys.stderr,
            )
            raise SystemExit(2)
        print(f"UpCloud API error ({code}): {message}", file=sys.stderr)
        raise SystemExit(1)

    if client_error_cls is not None and isinstance(exc, client_error_cls):
        # Base class: client-side config/credential problems.
        print(f"UpCloud client error: {exc}", file=sys.stderr)
        raise SystemExit(2)

    if isinstance(exc, ConnectionError | TimeoutError):
        print(f"Network error: {exc}", file=sys.stderr)
        raise SystemExit(1)

    if isinstance(exc, OSError):
        # requests.Timeout / ConnectionError subclass OSError, so this also
        # catches unwrapped network failures from the SDK's requests layer.
        print(f"OS error: {exc}", file=sys.stderr)
        raise SystemExit(1)

    # Anything we don't recognise gets re-raised as-is.
    raise exc


# ---------------------------------------------------------------------------
# Defensive retry / backoff — the SDK ships none.
# ---------------------------------------------------------------------------

# Substrings (case-insensitive) in error_code/message that mark a response
# as worth retrying. UpCloud has no documented 429 contract; we infer from
# the code/message since the status code is not on the exception.
_RETRYABLE_HINTS = (
    "too many requests",
    "rate",
    "throttl",
    "service unavailable",
    "internal server error",
    "try again",
    "temporarily",
)
_RETRYABLE_CODES = frozenset(
    {
        "TOO_MANY_REQUESTS",
        "RATE_LIMIT",
        "SERVICE_UNAVAILABLE",
        "INTERNAL_ERROR",
        "INTERNAL_SERVER_ERROR",
    }
)


def is_retryable(exc: BaseException) -> bool:
    """True if ``exc`` looks like a transient 429 / 5xx or network timeout.

    Status codes are not available on ``UpCloudAPIError`` (only
    ``error_code`` / ``error_message``), so we match those strings plus the
    raw ``requests`` timeout / connection exceptions, which subclass
    ``OSError`` / ``TimeoutError``.
    """
    if isinstance(exc, ConnectionError | TimeoutError | OSError):
        return True
    api_error_cls, _ = _upcloud_exc_classes()
    if api_error_cls is None:  # pragma: no cover - upcloud-api not installed
        return False
    if isinstance(exc, api_error_cls):
        code = (getattr(exc, "error_code", "") or "").upper()
        if code in _RETRYABLE_CODES:
            return True
        haystack = f"{code} {getattr(exc, 'error_message', '') or ''}".lower()
        return any(hint in haystack for hint in _RETRYABLE_HINTS)
    return False


def with_backoff(
    func: Callable[[], T],
    *,
    attempts: int = 4,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Call ``func()`` with exponential backoff on transient failures.

    Retries up to ``attempts`` times (so ``attempts - 1`` retries) on
    exceptions for which :func:`is_retryable` is true, sleeping
    ``min(base_delay * 2**n, max_delay)`` seconds between tries. Non-retryable
    exceptions propagate immediately; the last retryable exception is
    re-raised once the budget is exhausted. ``sleep`` is injectable so tests
    don't actually wait.
    """
    if attempts < 1:
        raise ValueError("attempts must be >= 1")
    last_exc: BaseException | None = None
    for n in range(attempts):
        try:
            return func()
        except Exception as exc:
            if not is_retryable(exc) or n == attempts - 1:
                raise
            last_exc = exc
            sleep(min(base_delay * (2**n), max_delay))
    # Unreachable: the loop either returns or raises. Defensive only.
    raise last_exc  # type: ignore[misc]  # pragma: no cover
