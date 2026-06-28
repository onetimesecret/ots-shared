# src/ots_shared/digitalocean/errors.py

"""Friendly error handling and retry/backoff for DigitalOcean API calls.

``pydo`` is OpenAPI-generated on top of ``azure-core``, so the API failure it
raises is :class:`azure.core.exceptions.HttpResponseError`. Unlike UpCloud's
exception (which carries only ``error_code`` / ``error_message``), the Azure
error **does** expose a ``status_code`` — the classifier keys off it for the
401/403 (auth) vs 429/5xx (transient) vs other (generic) mapping. Network-level
failures surface as :class:`azure.core.exceptions.ServiceRequestError` /
``ServiceResponseError`` (both ``AzureError`` subclasses) or as raw
``ConnectionError`` / ``TimeoutError``.

``azure-core`` retries some transients itself, but bounded and opaque to us, so
:func:`with_backoff` provides the same small, defensive exponential backoff the
UpCloud module does — honoring a ``Retry-After`` header when present.
"""

from __future__ import annotations

import contextlib
import sys
import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")

# 401/403 are credential/permission problems (exit 2); everything else the API
# rejects is a runtime API error (exit 1), mirroring the lots exit-code
# conventions (0 ok, 1 API, 2 config, 65 validation, 70 drift).
_AUTH_STATUS = frozenset({401, 403})
# Transient HTTP statuses worth retrying.
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


def _http_error_cls() -> type | None:
    """Return azure-core's HttpResponseError, or None if azure-core is absent.

    Guarded so a non-DigitalOcean install never hard-imports the Azure stack.
    """
    try:
        from azure.core.exceptions import HttpResponseError
    except ImportError:  # pragma: no cover - pydo/azure-core not installed
        return None
    return HttpResponseError


def _azure_error_cls() -> type | None:
    """Return azure-core's AzureError base (covers network-level failures)."""
    try:
        from azure.core.exceptions import AzureError
    except ImportError:  # pragma: no cover - pydo/azure-core not installed
        return None
    return AzureError


def _detail(exc: BaseException) -> str:
    """Best-effort human-readable detail from a pydo/azure error.

    DigitalOcean puts ``{"id", "message"}`` in the body, which azure-core
    surfaces on ``exc.error.message``; fall back to the exception's own message.
    """
    err = getattr(exc, "error", None)
    if err is not None:
        msg = getattr(err, "message", "") or ""
        if msg:
            return msg
    return getattr(exc, "message", "") or str(exc)


@contextlib.contextmanager
def api_errors():
    """Catch DigitalOcean API errors and exit with a user-friendly message.

    Maps ``HttpResponseError`` (by status), azure network errors, and raw
    network errors to friendly stderr lines and a deterministic exit code.
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
    msg = classify(exc)
    if msg is not None:
        print(msg, file=sys.stderr)
        # Auth problems exit 2; every other classified API/network error exits 1.
        raise SystemExit(2 if msg.startswith("DigitalOcean auth") else 1)
    raise exc


def classify(exc: BaseException) -> str | None:
    """Return a friendly one-line message for a known error, else ``None``.

    Kept separate from :func:`_handle` so a generalized multi-provider error
    boundary (and ``lots.cli``'s interim DO handler) can dispatch to it without
    inheriting the exit-code/printing policy.
    """
    http_cls = _http_error_cls()
    if http_cls is not None and isinstance(exc, http_cls):
        status = getattr(exc, "status_code", None)
        detail = _detail(exc)
        if status in _AUTH_STATUS:
            return (
                "DigitalOcean auth failed; check DIGITALOCEAN_TOKEN / "
                f"DIGITALOCEAN_ACCESS_TOKEN (HTTP {status}: {detail})"
            )
        status_part = f" {status}" if status is not None else ""
        return f"DigitalOcean API error (HTTP{status_part}): {detail}"

    azure_cls = _azure_error_cls()
    if azure_cls is not None and isinstance(exc, azure_cls):
        # AzureError that is not an HttpResponseError == network/transport.
        return f"Network error: {_detail(exc)}"

    if isinstance(exc, ConnectionError | TimeoutError):
        return f"Network error: {exc}"
    if isinstance(exc, OSError):
        return f"OS error: {exc}"
    return None


# ---------------------------------------------------------------------------
# Defensive retry / backoff.
# ---------------------------------------------------------------------------


def _retry_after_seconds(exc: BaseException) -> float | None:
    """Parse a ``Retry-After`` header (seconds) off an HttpResponseError."""
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    raw = headers.get("Retry-After") or headers.get("retry-after")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def is_retryable(exc: BaseException) -> bool:
    """True if ``exc`` looks like a transient 429 / 5xx or network failure."""
    if isinstance(exc, ConnectionError | TimeoutError | OSError):
        return True
    http_cls = _http_error_cls()
    if http_cls is not None and isinstance(exc, http_cls):
        return getattr(exc, "status_code", None) in _RETRYABLE_STATUS
    azure_cls = _azure_error_cls()
    if azure_cls is not None and isinstance(exc, azure_cls):
        # A non-HTTP AzureError is a transport-level (connect/read) failure.
        return http_cls is None or not isinstance(exc, http_cls)
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
    ``min(base_delay * 2**n, max_delay)`` seconds between tries — or the
    ``Retry-After`` value when the server provides one. Non-retryable
    exceptions propagate immediately; the last retryable exception is re-raised
    once the budget is exhausted. ``sleep`` is injectable so tests don't wait.
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
            delay = min(base_delay * (2**n), max_delay)
            retry_after = _retry_after_seconds(exc)
            if retry_after is not None:
                delay = min(max(retry_after, delay), max_delay)
            sleep(delay)
    # Unreachable: the loop either returns or raises. Defensive only.
    raise last_exc  # type: ignore[misc]  # pragma: no cover
