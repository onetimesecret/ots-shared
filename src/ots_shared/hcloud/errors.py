# packages/ots-shared/src/ots_shared/hcloud/errors.py

"""Friendly error handling for hcloud API calls."""

import contextlib
import sys


@contextlib.contextmanager
def api_errors():
    """Catch hcloud API errors and exit with a user-friendly message."""
    try:
        yield
    except SystemExit:
        raise
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as exc:
        _handle(exc)


def _handle(exc):
    """Map known exceptions to friendly messages, re-raise the rest."""
    from hcloud import APIException, HCloudException

    if isinstance(exc, APIException):
        # APIException carries code + message from the Hetzner API
        print(f"Hetzner API error ({exc.code}): {exc.message}", file=sys.stderr)
        raise SystemExit(1)

    if isinstance(exc, HCloudException):
        # ActionException and any future HCloudException subclasses
        print(f"Hetzner API error: {exc}", file=sys.stderr)
        raise SystemExit(1)

    if isinstance(exc, ConnectionError | TimeoutError):
        print(f"Network error: {exc}", file=sys.stderr)
        raise SystemExit(1)

    if isinstance(exc, OSError):
        print(f"OS error: {exc}", file=sys.stderr)
        raise SystemExit(1)

    # Anything we don't recognise gets re-raised as-is
    raise
