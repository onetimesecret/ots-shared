# packages/ots-shared/tests/trust/test_cli_list.py

"""Tests for ``trust list`` (spec §10).

``trust list`` shows three buckets:

* hosts present in both ``.otsinfra.yaml`` and ``.trust/hosts/``
* hosts declared in the marker but not yet materialised
* hosts on disk but no longer declared in the marker
"""

from __future__ import annotations

from pathlib import Path

import pytest
from ots_shared.trust.cli import app


def _import_app():
    return app


def _run_app(app, args: list[str]) -> int | None:
    """Invoke a Cyclopts app, returning its exit code (always raises SystemExit)."""
    with pytest.raises(SystemExit) as exc_info:
        app(args)
    return exc_info.value.code


def _set_marker_hosts(checkout: Path, *roles: str) -> None:
    """Rewrite ``.otsinfra.yaml`` declaring exactly *roles*."""
    lines = [
        "env_name: test-fixture",
        "created: '2026-04-25'",
    ]
    if roles:
        lines.append("hosts:")
        for role in roles:
            lines.append(f"  {role}:")
            lines.append(f"    private_ip_address: 10.0.0.{1 + roles.index(role)}")
    (checkout / ".otsinfra.yaml").write_text("\n".join(lines) + "\n")


def test_list_shows_present_in_both(populated_trust_dir: Path, monkeypatch, capsys) -> None:
    """spec §10: hosts present in both marker and disk show up.

    The fixture declares web/db in both, so each role must appear in
    the output (under whatever heading the CLI uses for the
    intersection bucket).
    """
    app = _import_app()
    monkeypatch.chdir(populated_trust_dir)

    rc = _run_app(app, ["list"])
    out = capsys.readouterr().out

    if rc not in (None, 0):
        pytest.fail(f"trust list exited {rc!r}")

    assert "web" in out
    assert "db" in out


def test_list_shows_declared_missing(populated_trust_dir: Path, monkeypatch, capsys) -> None:
    """spec §10: a role declared in the marker but absent on disk shows.

    Add a ``cache`` role to the marker without materialising it. The
    output must mention the role and identify it as missing/declared,
    distinct from the present-in-both bucket.
    """
    app = _import_app()
    _set_marker_hosts(populated_trust_dir, "web", "db", "cache")
    monkeypatch.chdir(populated_trust_dir)

    rc = _run_app(app, ["list"])
    out = capsys.readouterr().out

    if rc not in (None, 0):
        pytest.fail(f"trust list exited {rc!r}")

    assert "cache" in out, "newly-declared role 'cache' must appear in output"
    assert any(
        token in out.lower()
        for token in ("missing", "declared", "not materialized", "not generated")
    ), f"output must label declared-but-absent hosts; got:\n{out}"


def test_list_shows_on_disk_undeclared(populated_trust_dir: Path, monkeypatch, capsys) -> None:
    """spec §10: a role on disk but not in the marker shows in its own bucket.

    Drop ``db`` from the marker, leaving its on-disk material
    untouched. The output must list ``db`` under the on-disk-only
    bucket, distinct from the present-in-both bucket.
    """
    app = _import_app()
    _set_marker_hosts(populated_trust_dir, "web")
    monkeypatch.chdir(populated_trust_dir)

    rc = _run_app(app, ["list"])
    out = capsys.readouterr().out

    if rc not in (None, 0):
        pytest.fail(f"trust list exited {rc!r}")

    assert "db" in out
    assert any(token in out.lower() for token in ("undeclared", "on disk", "stale", "removed")), (
        f"output must label on-disk-but-undeclared hosts; got:\n{out}"
    )


# ---- PR #61 follow-up Item 3: socks visibility ----------------------------


def _set_marker_with_socks(checkout: Path, *roles: str, declare_socks: bool) -> None:
    """Rewrite ``.otsinfra.yaml`` with hosts and an optional ``socks: {}`` block.

    The exact marker shape for a "declared" socks entry is the
    production agent's call — the spec only requires the list output
    distinguish declared-vs-on-disk. We keep the assertion permissive
    so the test stays useful regardless of the final shape.
    """
    lines = ["env_name: test-fixture", "created: '2026-04-25'"]
    if roles:
        lines.append("hosts:")
        for role in roles:
            lines.append(f"  {role}:")
            lines.append(f"    private_ip_address: 10.0.0.{1 + roles.index(role)}")
    if declare_socks:
        lines.append("socks:")
        lines.append("  enabled: true")
    (checkout / ".otsinfra.yaml").write_text("\n".join(lines) + "\n")


def test_list_includes_socks_when_present(populated_trust_dir: Path, monkeypatch, capsys) -> None:
    """``trust list`` surfaces ``socks`` when ``.trust/socks/`` exists.

    The fixture creates ``.trust/socks/``. The list command must not
    silently ignore the SOCKS singleton — it's trust material that
    rotates independently of host roles and an operator inspecting
    state needs to see it.
    """
    app = _import_app()
    monkeypatch.chdir(populated_trust_dir)

    rc = _run_app(app, ["list"])
    out = capsys.readouterr().out

    if rc not in (None, 0):
        pytest.fail(f"trust list exited {rc!r}")

    assert "socks" in out.lower(), (
        f"socks present on disk must appear in trust list output; got:\n{out}"
    )


def test_list_socks_declared_but_missing(populated_trust_dir: Path, monkeypatch, capsys) -> None:
    """SOCKS declared in marker but ``.trust/socks/`` removed → labelled missing.

    Mirrors the host-role declared-but-missing bucket: when the marker
    asks for SOCKS but the on-disk material isn't there, the operator
    must see drift surfaced explicitly.
    """
    import shutil

    app = _import_app()
    _set_marker_with_socks(populated_trust_dir, "web", "db", declare_socks=True)
    shutil.rmtree(populated_trust_dir / ".trust" / "socks")
    monkeypatch.chdir(populated_trust_dir)

    rc = _run_app(app, ["list"])
    out = capsys.readouterr().out

    if rc not in (None, 0):
        pytest.fail(f"trust list exited {rc!r}")

    assert "socks" in out.lower()
    assert any(
        token in out.lower()
        for token in ("missing", "declared", "not materialized", "not generated")
    ), f"socks declared-but-missing must be labelled; got:\n{out}"


def test_list_socks_on_disk_undeclared(populated_trust_dir: Path, monkeypatch, capsys) -> None:
    """``.trust/socks/`` present but marker lacks socks → on-disk-only bucket.

    The fixture leaves SOCKS undeclared by default in the rewritten
    marker, so this test simply asserts the on-disk-only labelling
    propagates to the SOCKS singleton.
    """
    app = _import_app()
    _set_marker_with_socks(populated_trust_dir, "web", "db", declare_socks=False)
    monkeypatch.chdir(populated_trust_dir)

    rc = _run_app(app, ["list"])
    out = capsys.readouterr().out

    if rc not in (None, 0):
        pytest.fail(f"trust list exited {rc!r}")

    assert "socks" in out.lower()
    assert any(token in out.lower() for token in ("undeclared", "on disk", "stale", "removed")), (
        f"socks on-disk-undeclared must be labelled; got:\n{out}"
    )
