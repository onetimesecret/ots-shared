# tests/digitalocean/test_wait.py

"""Tests for ots_shared.digitalocean.wait (action polling)."""

from unittest.mock import MagicMock

import pytest

from ots_shared.digitalocean.wait import ProviderTimeout, wait_for_action


def _client_returning(statuses):
    """A MagicMock client whose actions.get walks ``statuses`` then repeats last."""
    seq = list(statuses)

    def get(action_id):
        status = seq.pop(0) if len(seq) > 1 else seq[0]
        return {"action": {"id": action_id, "status": status}}

    client = MagicMock()
    client.actions.get.side_effect = get
    return client


class TestWaitForAction:
    def test_returns_on_completed(self):
        client = _client_returning(["in-progress", "in-progress", "completed"])
        wait_for_action(client, 123, sleep=lambda _: None, now=_fake_clock())
        assert client.actions.get.call_count == 3

    def test_errored_raises_exit_1(self, capsys):
        client = _client_returning(["in-progress", "errored"])
        with pytest.raises(SystemExit) as exc_info:
            wait_for_action(client, 9, label="reboot", sleep=lambda _: None, now=_fake_clock())
        assert exc_info.value.code == 1
        assert "errored" in capsys.readouterr().err

    def test_timeout_raises_provider_timeout(self):
        client = _client_returning(["in-progress"])
        # now() jumps past the deadline on the second check.
        clock = iter([0.0, 0.0, 999.0, 999.0])
        with pytest.raises(ProviderTimeout) as exc_info:
            wait_for_action(
                client,
                5,
                timeout=600,
                sleep=lambda _: None,
                now=lambda: next(clock),
            )
        assert exc_info.value.code == 1
        assert "Timed out" in exc_info.value.message

    def test_completed_immediately_does_not_sleep(self):
        client = _client_returning(["completed"])
        slept = []
        wait_for_action(client, 1, sleep=slept.append, now=_fake_clock())
        assert slept == []


def _fake_clock():
    """A monotonic-ish clock that never trips the timeout."""
    t = {"v": 0.0}

    def now():
        t["v"] += 1.0
        return t["v"]

    return now
