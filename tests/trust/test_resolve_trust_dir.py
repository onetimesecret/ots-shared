# packages/ots-shared/tests/trust/test_resolve_trust_dir.py

"""Tests for ``ots_shared.trust.resolve_trust_dir`` (PR #61 follow-up, Item 2).

The shared helper consolidates two near-duplicate ``_resolve_trust_dir``
implementations that previously lived in ``lots/cloudinit/cli.py`` and
``lots/trust/cli.py``. It anchors off ``.otsinfra.yaml`` (not the
``.trust/`` dir) so a freshly-materialised checkout — or one where
``.trust/`` was just rmtree'd by ``--force`` — still resolves a stable
path. Existence of ``.trust/`` is the caller's responsibility.

Will fail until the production agent exports ``resolve_trust_dir`` and
``OtsInfraMarkerMissingError`` from ``ots_shared.trust``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ots_shared.trust import (  # type: ignore[import-not-found]
    OtsInfraMarkerMissingError,
    resolve_trust_dir,
)


def _write_marker(checkout: Path) -> None:
    (checkout / ".otsinfra.yaml").write_text("env_name: test\ncreated: '2026-04-25'\n")


# ---- C1: marker at start dir → trust dir alongside it --------------------


def test_resolves_trust_dir_when_marker_in_start_dir(tmp_path: Path) -> None:
    """Marker at ``tmp_path/.otsinfra.yaml`` → returns ``tmp_path/.trust``."""
    _write_marker(tmp_path)
    assert resolve_trust_dir(start=tmp_path) == tmp_path / ".trust"


# ---- C2: walk-up from a deep subdir --------------------------------------


def test_walks_up_from_deep_subdir(tmp_path: Path) -> None:
    """``resolve_trust_dir(start=tmp_path/sub/deep)`` walks up to the marker."""
    _write_marker(tmp_path)
    deep = tmp_path / "sub" / "deeper" / "leaf"
    deep.mkdir(parents=True)
    assert resolve_trust_dir(start=deep) == tmp_path / ".trust"


# ---- C3: no marker anywhere → typed exception ----------------------------


def test_no_marker_raises_typed_error(tmp_path: Path) -> None:
    """Walk-up exhausted without a marker → ``OtsInfraMarkerMissingError``.

    The error type must be distinct from ``TrustMaterialMissingError`` so
    callers can tell "no environment context" from "environment exists
    but trust state hasn't been materialised yet" — they prompt different
    operator actions.
    """
    # Use a subdir under tmp_path so walk-up doesn't escape into the
    # repo root and accidentally find a real marker.
    isolated = tmp_path / "isolated"
    isolated.mkdir()
    with pytest.raises(OtsInfraMarkerMissingError):
        resolve_trust_dir(start=isolated)


# ---- C4: .trust/ does not need to exist ---------------------------------


def test_returns_path_without_requiring_trust_dir_to_exist(tmp_path: Path) -> None:
    """The helper is path-resolution, not existence-check.

    Resolving when only the marker is present must succeed and return
    the would-be ``.trust/`` path. Caller (lots init / lots cloudinit
    render / lots trust list) decides what to do based on whether the
    path exists on disk.
    """
    _write_marker(tmp_path)
    # Sanity precondition.
    assert not (tmp_path / ".trust").exists()
    resolved = resolve_trust_dir(start=tmp_path)
    assert resolved == tmp_path / ".trust"
    # Post-condition unchanged: we did not create the directory.
    assert not (tmp_path / ".trust").exists(), (
        "resolve_trust_dir must not materialise the path it returns"
    )


# ---- bonus: default start = cwd -----------------------------------------


def test_default_start_uses_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``resolve_trust_dir()`` with no args uses cwd as the walk-up anchor.

    Mirrors the ``find_marker(start=None)`` convention elsewhere in
    ots-shared.
    """
    _write_marker(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert resolve_trust_dir() == tmp_path / ".trust"
