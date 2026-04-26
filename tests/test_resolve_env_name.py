# tests/test_resolve_env_name.py

"""Tests for ots_shared.ssh.env.resolve_env_name.

Precedence rules under test:
    - marker.env_name AND $ENV_NAME both set → must match exactly.
    - marker.env_name only → use it.
    - $ENV_NAME only (no marker.env_name) → MarkerEnvNameMissing.
    - neither → MarkerEnvNameMissing.
"""

from __future__ import annotations

import pytest

from ots_shared.ssh.env import (
    EnvNameConflict,
    MarkerEnvNameMissing,
    resolve_env_name,
)


def test_marker_only_returns_marker_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENV_NAME", raising=False)
    assert resolve_env_name({"env_name": "eu"}) == "eu"


def test_marker_and_env_match_returns_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENV_NAME", "eu")
    assert resolve_env_name({"env_name": "eu"}) == "eu"


def test_marker_and_env_conflict_raises_with_both_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENV_NAME", "ca")
    with pytest.raises(EnvNameConflict) as exc_info:
        resolve_env_name({"env_name": "eu"})
    msg = str(exc_info.value)
    assert "eu" in msg
    assert "ca" in msg


def test_env_only_no_marker_env_name_raises_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """$ENV_NAME alone is insufficient — the marker must declare env_name."""
    monkeypatch.setenv("ENV_NAME", "eu")
    with pytest.raises(MarkerEnvNameMissing):
        resolve_env_name({"hosts": {"db": {}}})


def test_neither_set_raises_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENV_NAME", raising=False)
    with pytest.raises(MarkerEnvNameMissing):
        resolve_env_name({"hosts": {"db": {}}})


def test_marker_none_raises_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENV_NAME", raising=False)
    with pytest.raises(MarkerEnvNameMissing):
        resolve_env_name(None)


def test_marker_empty_dict_raises_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENV_NAME", raising=False)
    with pytest.raises(MarkerEnvNameMissing):
        resolve_env_name({})
