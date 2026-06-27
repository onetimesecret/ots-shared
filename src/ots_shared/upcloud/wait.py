# src/ots_shared/upcloud/wait.py

"""State polling for UpCloud's action-less async model.

UpCloud has **no action/operation ids**. Lifecycle calls return the
resource; progress is read from the server's embedded ``state``. We poll
``manager.get_server(uuid).state`` rather than using the SDK's built-in
blocking ``Server.start(timeout=...)`` helpers so the retry/backoff
(see :mod:`ots_shared.upcloud.errors`) and the timeout ceiling stay
unified under our control.

Server states: ``started`` / ``stopped`` are terminal/settled,
``maintenance`` means in-progress (keep polling), ``error`` is a terminal
failure. Storage has its own ``state`` (``online`` / ``maintenance`` / ...)
read the same way via :func:`wait_for_storage_state`.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from typing import Any

from .errors import with_backoff

# A terminal failure state for servers. Reaching it raises immediately
# rather than burning the whole timeout budget.
_ERROR_STATE = "error"
_MAINTENANCE_STATE = "maintenance"


def _poll_state(
    fetch: Callable[[], Any],
    target: str,
    label: str,
    *,
    timeout: float,
    interval: float,
    sleep: Callable[[float], None],
    now: Callable[[], float],
) -> None:
    """Shared poll loop for server / storage waits.

    ``fetch`` returns a resource whose ``.state`` is inspected. Returns
    when ``state == target``; raises ``SystemExit(1)`` on ``error``; raises
    ``SystemExit`` (timeout) once ``timeout`` seconds elapse while still
    transitioning.

    Each ``fetch`` is wrapped in the bounded :func:`with_backoff` so a
    transient 429 / 5xx / network blip on a single poll is retried (briefly,
    bounded) rather than aborting the whole wait. The ``timeout`` deadline is
    still checked once per loop iteration, so the backoff cannot extend the
    overall wait past the ceiling by more than one bounded retry budget.
    """
    deadline = now() + timeout
    last_state: str | None = None
    while True:
        resource = with_backoff(fetch, sleep=sleep)
        state = getattr(resource, "state", None)
        last_state = state
        if state == target:
            return
        if state == _ERROR_STATE:
            print(
                f"{label} entered 'error' state while waiting for '{target}'",
                file=sys.stderr,
            )
            raise SystemExit(1)
        if now() >= deadline:
            print(
                f"Timed out after {timeout:.0f}s waiting for {label} to reach "
                f"'{target}' (last state: {last_state!r})",
                file=sys.stderr,
            )
            raise SystemExit(1)
        sleep(interval)


def wait_for_state(
    manager: Any,
    uuid: str,
    target: str,
    *,
    timeout: float = 600.0,
    interval: float = 10.0,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
) -> None:
    """Poll ``manager.get_server(uuid).state`` until it reaches ``target``.

    ``maintenance`` keeps polling; ``error`` raises ``SystemExit(1)``;
    exceeding ``timeout`` seconds raises ``SystemExit`` with a
    ProviderTimeout-style message. ``sleep`` / ``now`` are injectable so
    tests run without real waits.
    """
    _poll_state(
        lambda: manager.get_server(uuid),
        target,
        f"server {uuid}",
        timeout=timeout,
        interval=interval,
        sleep=sleep,
        now=now,
    )


def wait_for_storage_state(
    manager: Any,
    uuid: str,
    target: str,
    *,
    timeout: float = 600.0,
    interval: float = 10.0,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
) -> None:
    """Poll ``manager.get_storage(uuid).state`` until it reaches ``target``.

    Same semantics as :func:`wait_for_state` but for storage resources
    (online / maintenance / ...).
    """
    _poll_state(
        lambda: manager.get_storage(uuid),
        target,
        f"storage {uuid}",
        timeout=timeout,
        interval=interval,
        sleep=sleep,
        now=now,
    )


def print_wait(msg: str) -> None:  # pragma: no cover - trivial diagnostic
    """Emit a wait diagnostic to stderr so stdout stays clean for --json."""
    print(msg, file=sys.stderr)
