# packages/ots-shared/tests/trust/test_cli_prune.py

"""Tests for ``trust prune`` (spec §8).

Prune is the explicit removal path — there is no implicit garbage
collection (spec §59). Safety defaults: refuse to prune the CA, refuse
to prune a still-declared host without an explicit override flag,
require ``--yes`` (or a TTY) for non-interactive removal.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ots_shared.trust.cli import app
from ots_shared.trust.manifest import Manifest


def _import_app():
    return app


def _run_app(app, args: list[str]) -> int | None:
    """Invoke a Cyclopts app, returning its exit code (always raises SystemExit)."""
    with pytest.raises(SystemExit) as exc_info:
        app(args)
    return exc_info.value.code


def _set_marker_hosts(checkout: Path, *roles: str) -> None:
    lines = [
        "env_name: test-fixture",
        "created: '2026-04-25'",
    ]
    if roles:
        lines.append("hosts:")
        for role in roles:
            lines.append(f"  {role}:")
            lines.append(f"    private_ip_address: 10.0.0.{1 + roles.index(role)}")
    (checkout / "otsinfra.yaml").write_text("\n".join(lines) + "\n")


# ---- happy path -----------------------------------------------------------


def test_prune_deletes_host_material_and_manifest_entries(
    populated_trust_dir: Path, monkeypatch, capsys
) -> None:
    """spec §8: prune <host> --yes removes the host directory and entries.

    After the run:
      - ``.trust/hosts/web/`` no longer exists.
      - ``.trust/manifest.yaml`` has no entries with ``name='web'``.
    """
    app = _import_app()
    # Drop web from the marker so the safety guard doesn't refuse —
    # explicit-removal paths require a confirmed undeclared state.
    _set_marker_hosts(populated_trust_dir, "db")
    monkeypatch.chdir(populated_trust_dir)

    rc = _run_app(app, ["prune", "web", "--yes"])
    capsys.readouterr()
    if rc not in (None, 0):
        pytest.fail(f"trust prune web --yes exited {rc!r}")

    web_dir = populated_trust_dir / ".trust" / "hosts" / "web"
    assert not web_dir.exists(), f"{web_dir} must be removed by prune"

    manifest = Manifest.load(populated_trust_dir / ".trust" / "manifest.yaml")
    web_entries = [e for e in manifest.entries if e.name == "web"]
    assert not web_entries, f"manifest must not retain entries for pruned 'web'; saw: {web_entries}"


def test_prune_other_hosts_untouched(populated_trust_dir: Path, monkeypatch, capsys) -> None:
    """spec §8: pruning role X leaves role Y on disk and in manifest."""
    app = _import_app()
    _set_marker_hosts(populated_trust_dir, "db")  # leaving 'web' undeclared
    monkeypatch.chdir(populated_trust_dir)

    rc = _run_app(app, ["prune", "web", "--yes"])
    capsys.readouterr()
    if rc not in (None, 0):
        pytest.fail(f"trust prune web --yes exited {rc!r}")

    db_dir = populated_trust_dir / ".trust" / "hosts" / "db"
    assert db_dir.is_dir(), "untargeted host 'db' must remain on disk"
    for name in ("ssh", "ssh.pub", "cert.pem", "key.pem", "wg", "wg.pub"):
        assert (db_dir / name).is_file(), f"db/{name} must remain after pruning web"

    manifest = Manifest.load(populated_trust_dir / ".trust" / "manifest.yaml")
    db_entries = [e for e in manifest.entries if e.name == "db"]
    assert db_entries, "manifest must still have 'db' entries"


# ---- safety guards --------------------------------------------------------


def test_prune_refuses_ca_role(populated_trust_dir: Path, monkeypatch, capsys) -> None:
    """spec §8: refusing to prune the CA. Pruning the root would brick trust."""
    app = _import_app()
    monkeypatch.chdir(populated_trust_dir)

    rc = _run_app(app, ["prune", "ca", "--yes"])
    captured = capsys.readouterr()

    assert rc not in (0, None), "prune ca must exit nonzero — pruning the CA is unsafe"
    # CA dir must be untouched.
    assert (populated_trust_dir / ".trust" / "ca" / "ca.crt").is_file()
    assert captured.err.strip() or captured.out.strip(), "must surface a refusal message"


def test_prune_refuses_declared_host_without_flag(
    populated_trust_dir: Path, monkeypatch, capsys
) -> None:
    """Safety default: refusing to prune a host still declared in otsinfra.yaml.

    The fixture leaves ``web`` declared. Without ``--declared-ok`` the
    command must refuse and leave the host material in place.
    """
    app = _import_app()
    monkeypatch.chdir(populated_trust_dir)
    # Sanity: confirm web is declared.
    assert "web:" in (populated_trust_dir / "otsinfra.yaml").read_text()

    rc = _run_app(app, ["prune", "web", "--yes"])
    capsys.readouterr()

    assert rc not in (0, None), "prune of a still-declared host must refuse without --declared-ok"
    assert (populated_trust_dir / ".trust" / "hosts" / "web").is_dir()


# ---- PR #61 follow-up Item 4: socks dir cleanup --------------------------


def test_prune_socks_removes_dir_and_manifest_entries(
    populated_trust_dir: Path, monkeypatch, capsys
) -> None:
    """``trust prune socks --yes`` removes ``.trust/socks/`` and entries.

    SOCKS is a singleton trust artifact (not under ``hosts/``), so
    pruning it deletes the ``.trust/socks/`` directory directly and
    drops manifest rows where ``name == "socks"``.
    """
    app = _import_app()
    monkeypatch.chdir(populated_trust_dir)

    rc = _run_app(app, ["prune", "socks", "--yes"])
    capsys.readouterr()
    if rc not in (None, 0):
        pytest.fail(f"trust prune socks --yes exited {rc!r}")

    socks_dir = populated_trust_dir / ".trust" / "socks"
    assert not socks_dir.exists(), f"{socks_dir} must be removed by prune socks"

    manifest = Manifest.load(populated_trust_dir / ".trust" / "manifest.yaml")
    socks_entries = [e for e in manifest.entries if e.name == "socks"]
    assert not socks_entries, (
        f"manifest must not retain socks entries after prune; saw: {socks_entries}"
    )


def test_prune_web_still_removes_hosts_subdir(
    populated_trust_dir: Path, monkeypatch, capsys
) -> None:
    """Regression: pruning a host-role still targets ``.trust/hosts/<role>/``.

    Confirms the role-dispatch refactor (socks vs hosts) did not break
    the existing host-role path.
    """
    app = _import_app()
    _set_marker_hosts(populated_trust_dir, "db")  # web undeclared
    monkeypatch.chdir(populated_trust_dir)

    rc = _run_app(app, ["prune", "web", "--yes"])
    capsys.readouterr()
    if rc not in (None, 0):
        pytest.fail(f"trust prune web --yes exited {rc!r}")

    web_dir = populated_trust_dir / ".trust" / "hosts" / "web"
    assert not web_dir.exists()
    # And confirm we didn't accidentally rmtree .trust/hosts/.
    assert (populated_trust_dir / ".trust" / "hosts").is_dir()


# ---- _role_dir module-level resolver -------------------------------------


def test_role_dir_socks_lives_under_trust_root() -> None:
    """``_role_dir(trust, "socks")`` returns ``trust/socks`` (singleton path)."""
    from pathlib import Path as _Path

    from ots_shared.trust.cli import _role_dir

    trust = _Path("/tmp/fake-trust")
    assert _role_dir(trust, "socks") == trust / "socks"


def test_role_dir_host_role_lives_under_hosts_subdir() -> None:
    """``_role_dir(trust, "<role>")`` returns ``trust/hosts/<role>`` for host roles."""
    from pathlib import Path as _Path

    from ots_shared.trust.cli import _role_dir

    trust = _Path("/tmp/fake-trust")
    assert _role_dir(trust, "web") == trust / "hosts" / "web"
    assert _role_dir(trust, "db") == trust / "hosts" / "db"


def test_prune_requires_yes_in_non_tty(populated_trust_dir: Path, monkeypatch, capsys) -> None:
    """Safety default: non-TTY invocation without --yes must refuse.

    The CLI is expected to consult ``sys.stdin.isatty()``. We patch it
    to return False (capturing the typical CI / pipe shape) and verify
    that omitting ``--yes`` produces a nonzero exit and leaves the
    host material in place.
    """
    app = _import_app()
    _set_marker_hosts(populated_trust_dir, "db")  # web undeclared
    monkeypatch.chdir(populated_trust_dir)

    import sys

    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)

    rc = _run_app(app, ["prune", "web"])
    capsys.readouterr()

    assert rc not in (0, None), "non-TTY prune without --yes must refuse"
    assert (populated_trust_dir / ".trust" / "hosts" / "web").is_dir(), (
        "host material must remain when prune was refused"
    )
