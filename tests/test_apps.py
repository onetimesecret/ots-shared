# tests/test_apps.py

"""Tests for ots_shared.apps.get_app_config.

Contract under test: ``None`` only when the top-level ``apps`` key is
entirely absent; any present-but-malformed block raises AppConfigError
with a message that names the offending field. Import contract is
pinned — other packages import exactly
``from ots_shared.apps import AppConfig, AppConfigError, get_app_config``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ots_shared.apps import AppConfig, AppConfigError, get_app_config
from ots_shared.ssh.env import load_marker

# examples/environment lives in the monorepo superproject, two levels
# above this submodule. Skip (not fail) when testing ots-shared
# standalone in its own repo.
_EXAMPLE_MARKER = Path(__file__).resolve().parents[3] / "examples" / "environment" / "otsinfra.yaml"


def make_block(**overrides: Any) -> dict:
    """Marker with a canonical apps block; overrides patch onetimesecret."""
    ots: dict[str, Any] = {
        "tag": "v0.26.0-rc2",
        "web_ports": [7043],
        "workers": 1,
        "scheduler": True,
    }
    ots.update(overrides)
    return {"env_name": "eu", "apps": {"onetimesecret": ots}}


# ---------------------------------------------------------------------------
# Feature-not-adopted vs adopted-but-broken
# ---------------------------------------------------------------------------


def test_absent_apps_key_returns_none() -> None:
    assert get_app_config({"env_name": "eu", "hosts": {"db": {}}}) is None


@pytest.mark.parametrize("apps", [None, "onetimesecret", ["onetimesecret"], 1])
def test_apps_not_a_mapping_raises(apps: Any) -> None:
    with pytest.raises(AppConfigError, match="'apps' must be a mapping"):
        get_app_config({"apps": apps})


def test_apps_present_but_onetimesecret_missing_raises() -> None:
    with pytest.raises(AppConfigError, match="no 'onetimesecret' entry"):
        get_app_config({"apps": {"otherapp": {}}})


def test_empty_apps_mapping_raises() -> None:
    """An empty ``apps: {}`` is adopted-but-broken, not feature-absent."""
    with pytest.raises(AppConfigError, match="no 'onetimesecret' entry"):
        get_app_config({"apps": {}})


@pytest.mark.parametrize("block", [None, "v0.26.0", [1, 2]])
def test_onetimesecret_not_a_mapping_raises(block: Any) -> None:
    with pytest.raises(AppConfigError, match="onetimesecret must be a mapping"):
        get_app_config({"apps": {"onetimesecret": block}})


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_valid_block_round_trips() -> None:
    cfg = get_app_config(make_block())
    assert cfg == AppConfig(tag="v0.26.0-rc2", web_ports=(7043,), workers=1, scheduler=True)


def test_defaults_workers_1_scheduler_true() -> None:
    marker = {"apps": {"onetimesecret": {"tag": "v1.0.0", "web_ports": [7043, 7044]}}}
    cfg = get_app_config(marker)
    assert cfg is not None
    assert cfg.workers == 1
    assert cfg.scheduler is True
    assert cfg.web_ports == (7043, 7044)


def test_explicit_overrides() -> None:
    cfg = get_app_config(make_block(workers=0, scheduler=False))
    assert cfg is not None
    assert cfg.workers == 0
    assert cfg.scheduler is False


def test_appconfig_is_frozen() -> None:
    cfg = get_app_config(make_block())
    assert cfg is not None
    with pytest.raises(AttributeError):
        cfg.tag = "v9.9.9"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# tag validation
# ---------------------------------------------------------------------------


def test_missing_tag_raises() -> None:
    marker = {"apps": {"onetimesecret": {"web_ports": [7043]}}}
    with pytest.raises(AppConfigError, match=r"tag is required but missing"):
        get_app_config(marker)


@pytest.mark.parametrize("tag", ["", None, 26, True, ["v1"]])
def test_non_string_or_empty_tag_raises(tag: Any) -> None:
    with pytest.raises(AppConfigError, match="tag must be a non-empty str"):
        get_app_config(make_block(tag=tag))


@pytest.mark.parametrize("tag", ["@current", "@latest"])
def test_alias_tag_rejected(tag: str) -> None:
    """Alias tags need registry resolution — offline render forbids them."""
    with pytest.raises(AppConfigError, match="alias tag"):
        get_app_config(make_block(tag=tag))


@pytest.mark.parametrize("tag", ["v1 .0", "-leading-dash", ".leading-dot", "a" * 129])
def test_implausible_tag_rejected(tag: str) -> None:
    with pytest.raises(AppConfigError, match="not a valid image tag"):
        get_app_config(make_block(tag=tag))


# ---------------------------------------------------------------------------
# web_ports validation
# ---------------------------------------------------------------------------


def test_missing_web_ports_raises() -> None:
    marker = {"apps": {"onetimesecret": {"tag": "v1.0.0"}}}
    with pytest.raises(AppConfigError, match=r"web_ports is required but missing"):
        get_app_config(marker)


@pytest.mark.parametrize("ports", [[], None, 7043, "7043", (7043,)])
def test_web_ports_wrong_shape_raises(ports: Any) -> None:
    with pytest.raises(AppConfigError, match="web_ports must be a non-empty list"):
        get_app_config(make_block(web_ports=ports))


@pytest.mark.parametrize("ports", [["7043"], [7043, None], [True]])
def test_web_ports_non_int_entries_raise(ports: list) -> None:
    with pytest.raises(AppConfigError, match="entries must be ints"):
        get_app_config(make_block(web_ports=ports))


@pytest.mark.parametrize("port", [0, -1, 65536])
def test_web_ports_out_of_range_raises(port: int) -> None:
    with pytest.raises(AppConfigError, match="valid port range"):
        get_app_config(make_block(web_ports=[port]))


def test_web_ports_duplicates_raise() -> None:
    with pytest.raises(AppConfigError, match="duplicates"):
        get_app_config(make_block(web_ports=[7043, 7043]))


# ---------------------------------------------------------------------------
# workers / scheduler / unknown keys
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("workers", ["1", None, 1.5, True])
def test_workers_wrong_type_raises(workers: Any) -> None:
    with pytest.raises(AppConfigError, match="workers must be an int"):
        get_app_config(make_block(workers=workers))


def test_negative_workers_raises() -> None:
    with pytest.raises(AppConfigError, match="workers must be >= 0"):
        get_app_config(make_block(workers=-1))


@pytest.mark.parametrize("scheduler", ["true", 1, None])
def test_scheduler_wrong_type_raises(scheduler: Any) -> None:
    with pytest.raises(AppConfigError, match="scheduler must be a bool"):
        get_app_config(make_block(scheduler=scheduler))


def test_unknown_key_raises_naming_it() -> None:
    """A typo'd key (web_port) must fail loud, not silently use defaults."""
    with pytest.raises(AppConfigError, match=r"unknown key\(s\) \['web_port'\]"):
        get_app_config(make_block(web_port=[7043]))


# ---------------------------------------------------------------------------
# Example marker integration
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _EXAMPLE_MARKER.is_file(),
    reason="examples/environment/otsinfra.yaml requires the monorepo superproject checkout",
)
def test_example_marker_parses_through_loader() -> None:
    """The shipped example must round-trip load_marker + get_app_config."""
    marker = load_marker(_EXAMPLE_MARKER)
    cfg = get_app_config(marker)
    assert cfg == AppConfig(tag="v0.26.0-rc2", web_ports=(7043,), workers=1, scheduler=True)
