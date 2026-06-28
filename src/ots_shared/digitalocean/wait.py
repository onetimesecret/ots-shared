# src/ots_shared/digitalocean/wait.py

"""Action polling for DigitalOcean's async-action model.

DigitalOcean mutations (power/reboot/rebuild/enable_backups, and create when
``--wait`` is passed) return an **action** with an id; progress is read by
polling ``actions.get(action_id)`` until the action reaches a terminal
``completed`` / ``errored`` status. There is **no** ``BoundAction.
wait_until_finished`` equivalent (the Hetzner contract) — multi-provider.md §5b —
so we poll manually with a hard timeout.

Without the timeout a stuck DO action would hang ``lots deploy`` forever, so a
timeout raises :class:`ProviderTimeout` (a ``SystemExit`` subclass). Each poll
is wrapped in the bounded :func:`ots_shared.digitalocean.errors.with_backoff` so
a transient 429 / 5xx on one poll is retried rather than aborting the wait.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from typing import Any

from .errors import with_backoff

# DigitalOcean action statuses.
_COMPLETED = "completed"
_ERRORED = "errored"
_IN_PROGRESS = "in-progress"


class ProviderTimeout(SystemExit):
    """Raised when a DigitalOcean action does not settle within the deadline.

    Subclasses ``SystemExit`` (exit code 1) so it propagates cleanly through the
    ``api_errors`` boundary as a fail-loud stall, distinct from an API error.
    """

    def __init__(self, message: str):
        super().__init__(1)
        self.message = message


def _action_status(action: Any) -> str | None:
    """Pull ``status`` out of an ``actions.get`` envelope or a bare action dict."""
    if isinstance(action, dict):
        inner = action.get("action", action)
        if isinstance(inner, dict):
            return inner.get("status")
    return None


def wait_for_action(
    client: Any,
    action_id: int,
    *,
    label: str = "action",
    timeout: float = 600.0,
    interval: float = 5.0,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
) -> None:
    """Poll ``client.actions.get(action_id)`` until it reaches a terminal state.

    Returns on ``completed``; raises ``SystemExit(1)`` on ``errored``; raises
    :class:`ProviderTimeout` once ``timeout`` seconds elapse while the action is
    still ``in-progress``. ``sleep`` / ``now`` are injectable so tests run
    without real waits.
    """
    deadline = now() + timeout
    last_status: str | None = None
    while True:
        envelope = with_backoff(lambda: client.actions.get(action_id), sleep=sleep)
        status = _action_status(envelope)
        last_status = status
        if status == _COMPLETED:
            return
        if status == _ERRORED:
            print(
                f"{label} (action {action_id}) entered 'errored' state",
                file=sys.stderr,
            )
            raise SystemExit(1)
        if now() >= deadline:
            raise ProviderTimeout(
                f"Timed out after {timeout:.0f}s waiting for {label} "
                f"(action {action_id}) to complete (last status: {last_status!r})"
            )
        sleep(interval)


def print_wait(msg: str) -> None:  # pragma: no cover - trivial diagnostic
    """Emit a wait diagnostic to stderr so stdout stays clean for --json."""
    print(msg, file=sys.stderr)
