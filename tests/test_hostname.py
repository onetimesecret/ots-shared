# tests/test_hostname.py

"""Tests for ots_shared.ssh.hostname.parse_hostname.

Contract under test: a strict <env>-<role>-<ordinal?> parser that uses
the ``hosts`` keys in a marker dict to identify the role via
longest-suffix match. Validates the env prefix against the resolved
env_name (which itself folds in $ENV_NAME via resolve_env_name).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

import pytest

from ots_shared.ssh.env import (
    EnvNameConflict,
    MarkerEnvNameMissing,
)
from ots_shared.ssh.hostname import (
    HostnameEmptyEnv,
    HostnameEnvMismatch,
    HostnameError,
    HostnameNoRoleMatch,
    ParsedHostname,
    parse_hostname,
)


@pytest.fixture(autouse=True)
def _clear_env_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip any inherited $ENV_NAME so resolve_env_name uses the marker only.

    Without this, a developer's shell or CI runner with ENV_NAME set would
    cause every happy-path test to raise EnvNameConflict.
    """
    monkeypatch.delenv("ENV_NAME", raising=False)


@pytest.fixture
def make_marker() -> Callable[..., dict]:
    """Factory for the canonical marker dict.

    Defaults to ``env_name='eu'`` with a single ``db`` host. Tests pass
    iterables of host role names plus an env_name override.
    """

    def _make(env_name: str = "eu", hosts: Iterable[str] = ("db",)) -> dict:
        return {"env_name": env_name, "hosts": {role: {} for role in hosts}}

    return _make


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_simple_env_role_ordinal(make_marker: Callable[..., dict]) -> None:
    result = parse_hostname("eu-db-01", make_marker(env_name="eu", hosts=("db",)))
    assert isinstance(result, ParsedHostname)
    assert result.env == "eu"
    assert result.role == "db"
    assert result.ordinal == "01"


def test_multi_segment_env(make_marker: Callable[..., dict]) -> None:
    result = parse_hostname(
        "eu-example-db-01", make_marker(env_name="eu-example", hosts=("db",))
    )
    assert result.env == "eu-example"
    assert result.role == "db"
    assert result.ordinal == "01"


def test_longest_suffix_match_wins(make_marker: Callable[..., dict]) -> None:
    """When both ``db`` and ``example-db`` are valid roles, the longer suffix wins."""
    result = parse_hostname(
        "eu-example-db-01", make_marker(env_name="eu", hosts=("db", "example-db"))
    )
    assert result.env == "eu"
    assert result.role == "example-db"
    assert result.ordinal == "01"


def test_jumphost_role(make_marker: Callable[..., dict]) -> None:
    result = parse_hostname(
        "eu-jumphost-01", make_marker(env_name="eu", hosts=("jumphost",))
    )
    assert result.env == "eu"
    assert result.role == "jumphost"
    assert result.ordinal == "01"


def test_no_trailing_digits_default_ordinal(make_marker: Callable[..., dict]) -> None:
    """No trailing digit segment → ordinal defaults to '01'."""
    result = parse_hostname(
        "ots-web-test", make_marker(env_name="ots-web", hosts=("test",))
    )
    assert result.env == "ots-web"
    assert result.role == "test"
    assert result.ordinal == "01"


def test_no_trailing_digits_multi_token_role(make_marker: Callable[..., dict]) -> None:
    result = parse_hostname(
        "ots-web-test", make_marker(env_name="ots", hosts=("web-test",))
    )
    assert result.env == "ots"
    assert result.role == "web-test"
    assert result.ordinal == "01"


# ---------------------------------------------------------------------------
# Failure paths — assert the SPECIFIC typed error
# ---------------------------------------------------------------------------


def test_empty_env_raises(make_marker: Callable[..., dict]) -> None:
    with pytest.raises(HostnameEmptyEnv):
        parse_hostname("db-01", make_marker(env_name="eu", hosts=("db",)))


def test_no_role_match_raises(make_marker: Callable[..., dict]) -> None:
    with pytest.raises(HostnameNoRoleMatch):
        parse_hostname("eu-foo-01", make_marker(env_name="eu", hosts=("db",)))


def test_env_mismatch_raises_with_both_values_in_message(
    make_marker: Callable[..., dict],
) -> None:
    with pytest.raises(HostnameEnvMismatch) as exc_info:
        parse_hostname("eu-db-01", make_marker(env_name="ca", hosts=("db",)))
    msg = str(exc_info.value)
    assert "eu" in msg
    assert "ca" in msg


def test_marker_missing_env_name_raises() -> None:
    """A marker without env_name (and no $ENV_NAME) must raise MarkerEnvNameMissing."""
    marker = {"hosts": {"db": {}}}
    with pytest.raises(MarkerEnvNameMissing):
        parse_hostname("eu-db-01", marker)


def test_env_name_conflict_via_parse_hostname(
    make_marker: Callable[..., dict],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """$ENV_NAME disagreeing with marker['env_name'] must raise EnvNameConflict.

    The autouse ``_clear_env_name`` fixture deletes ENV_NAME before this test
    runs; setting it again here (via the same per-test monkeypatch instance)
    overrides that for the duration of this single test. Exercises the
    EnvNameConflict path in resolve_env_name through parse_hostname.
    """
    monkeypatch.setenv("ENV_NAME", "wrong-env")
    marker = make_marker(env_name="eu", hosts=("db",))
    with pytest.raises(EnvNameConflict) as exc_info:
        parse_hostname("eu-db-01", marker)
    msg = str(exc_info.value)
    assert "wrong-env" in msg
    assert "eu" in msg


def test_marker_missing_env_name_with_env_var_set_via_parse_hostname(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Marker without env_name still raises MarkerEnvNameMissing even when $ENV_NAME is set.

    The contract: marker is the source of truth; $ENV_NAME alone is not enough.
    """
    monkeypatch.setenv("ENV_NAME", "eu")
    marker = {"hosts": {"db": {}}}
    with pytest.raises(MarkerEnvNameMissing):
        parse_hostname("eu-db-01", marker)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("hostname", "expected_ordinal"),
    [
        ("eu-db-99", "99"),
        ("eu-db-100", "100"),
        ("eu-db-1", "1"),
    ],
)
def test_ordinal_preserved_verbatim(
    make_marker: Callable[..., dict], hostname: str, expected_ordinal: str
) -> None:
    """Ordinal is preserved as-is — no zero-padding, no normalization."""
    result = parse_hostname(hostname, make_marker(env_name="eu", hosts=("db",)))
    assert result.ordinal == expected_ordinal


def test_empty_hostname_raises_some_hostname_error(
    make_marker: Callable[..., dict],
) -> None:
    with pytest.raises(HostnameError):
        parse_hostname("", make_marker(env_name="eu", hosts=("db",)))


def test_leading_hyphen_raises(make_marker: Callable[..., dict]) -> None:
    with pytest.raises(HostnameError):
        parse_hostname("-eu-db-01", make_marker(env_name="eu", hosts=("db",)))


def test_trailing_hyphen_raises(make_marker: Callable[..., dict]) -> None:
    with pytest.raises(HostnameError):
        parse_hostname("eu-db-01-", make_marker(env_name="eu", hosts=("db",)))


def test_empty_hosts_raises_no_role_match() -> None:
    marker = {"env_name": "eu", "hosts": {}}
    with pytest.raises(HostnameNoRoleMatch):
        parse_hostname("eu-db-01", marker)


def test_error_hierarchy() -> None:
    """All five typed errors inherit from HostnameError; HostnameError extends RuntimeError."""
    assert issubclass(HostnameError, RuntimeError)
    assert issubclass(HostnameNoRoleMatch, HostnameError)
    assert issubclass(HostnameEmptyEnv, HostnameError)
    assert issubclass(HostnameEnvMismatch, HostnameError)
    assert issubclass(MarkerEnvNameMissing, HostnameError)
    assert issubclass(EnvNameConflict, HostnameError)
