# tests/upcloud/test_wait.py

"""Tests for ots_shared.upcloud.wait.

UpCloud has no action ids; progress is read from server.state. We drive
the poll loop with a sequence of fake states and injectable sleep/clock so
tests run instantly.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ots_shared.upcloud.wait import wait_for_state, wait_for_storage_state


def _server_with_states(states):
    """A manager whose get_server returns objects with the given states."""
    objs = [MagicMock(state=s) for s in states]
    mgr = MagicMock()
    mgr.get_server.side_effect = objs
    return mgr


class _FakeClock:
    """Monotonic clock that advances by `step` on each call."""

    def __init__(self, step=1.0):
        self.t = 0.0
        self.step = step

    def __call__(self):
        cur = self.t
        self.t += self.step
        return cur


class TestWaitForState:
    def test_immediate_target(self):
        mgr = _server_with_states(["started"])
        wait_for_state(mgr, "uuid-1", "started", sleep=lambda _: None, now=_FakeClock())
        assert mgr.get_server.call_count == 1

    def test_maintenance_then_target(self):
        mgr = _server_with_states(["maintenance", "maintenance", "started"])
        sleeps = []
        wait_for_state(
            mgr,
            "uuid-1",
            "started",
            interval=5,
            sleep=sleeps.append,
            now=_FakeClock(),
        )
        assert mgr.get_server.call_count == 3
        assert sleeps == [5, 5]

    def test_error_state_fail_loud(self, capsys):
        mgr = _server_with_states(["maintenance", "error"])
        with pytest.raises(SystemExit) as exc:
            wait_for_state(mgr, "uuid-1", "started", sleep=lambda _: None, now=_FakeClock())
        assert exc.value.code == 1
        assert "error" in capsys.readouterr().err

    def test_timeout_while_transitioning(self, capsys):
        # Always maintenance; the clock blows past the deadline.
        mgr = MagicMock()
        mgr.get_server.return_value = MagicMock(state="maintenance")
        clock = _FakeClock(step=5.0)
        with pytest.raises(SystemExit) as exc:
            wait_for_state(
                mgr,
                "uuid-1",
                "started",
                timeout=10.0,
                interval=1,
                sleep=lambda _: None,
                now=clock,
            )
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "Timed out" in err
        assert "maintenance" in err

    def test_stop_target(self):
        mgr = _server_with_states(["maintenance", "stopped"])
        wait_for_state(mgr, "uuid-1", "stopped", sleep=lambda _: None, now=_FakeClock())
        assert mgr.get_server.call_count == 2


class TestWaitForStorageState:
    def test_storage_online(self):
        mgr = MagicMock()
        mgr.get_storage.side_effect = [MagicMock(state="maintenance"), MagicMock(state="online")]
        wait_for_storage_state(mgr, "st-1", "online", sleep=lambda _: None, now=_FakeClock())
        assert mgr.get_storage.call_count == 2

    def test_storage_error_fail_loud(self):
        mgr = MagicMock()
        mgr.get_storage.return_value = MagicMock(state="error")
        with pytest.raises(SystemExit) as exc:
            wait_for_storage_state(mgr, "st-1", "online", sleep=lambda _: None, now=_FakeClock())
        assert exc.value.code == 1


class TestPollLoopBackoffWiring:
    """The poll fetch is wrapped in the bounded with_backoff (M2 wiring).

    A transient 429/5xx/network blip on a single ``get_server`` poll must be
    retried (bounded) rather than aborting the whole wait.
    """

    def test_transient_fetch_error_is_retried_then_polls(self):
        from upcloud_api import UpCloudAPIError

        # First fetch raises a retryable 429, then a maintenance, then started.
        results = [
            UpCloudAPIError("TOO_MANY_REQUESTS", "slow down"),
            MagicMock(state="maintenance"),
            MagicMock(state="started"),
        ]

        def _fetch(_uuid):
            item = results.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        mgr = MagicMock()
        mgr.get_server.side_effect = _fetch
        sleeps = []
        wait_for_state(
            mgr,
            "uuid-1",
            "started",
            interval=5,
            sleep=sleeps.append,
            now=_FakeClock(),
        )
        # 3 fetches total (1 raised+retried, 1 maintenance, 1 started).
        assert mgr.get_server.call_count == 3
        # A backoff sleep (base_delay default 1.0) fired for the retried fetch,
        # plus the inter-poll interval sleep (5) after maintenance.
        assert 1.0 in sleeps
        assert 5 in sleeps

    def test_non_retryable_fetch_error_aborts_wait(self):
        """A non-transient API error during a poll is NOT retried — it raises."""
        from upcloud_api import UpCloudAPIError

        mgr = MagicMock()
        mgr.get_server.side_effect = UpCloudAPIError("SERVER_NOT_FOUND", "gone")
        with pytest.raises(UpCloudAPIError):
            wait_for_state(mgr, "uuid-1", "started", sleep=lambda _: None, now=_FakeClock())
        assert mgr.get_server.call_count == 1
