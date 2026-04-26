# packages/ots-shared/tests/trust/test_cli_fingerprints.py

"""Tests for ``trust fingerprints`` (AC #4).

The command dumps fingerprints from the manifest in a deterministic
order. Each printed fingerprint must match the corresponding manifest
entry's fingerprint.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from ots_shared.trust.cli import app


def _import_app():
    return app


def _run_app(app, args: list[str]) -> int | None:
    """Invoke a Cyclopts app and return its exit code.

    Cyclopts apps raise ``SystemExit`` on completion (success and
    failure both). This helper centralises the wrapping so callers
    can focus on assertions over output and side effects.
    """
    with pytest.raises(SystemExit) as exc_info:
        app(args)
    return exc_info.value.code


def test_fingerprints_match_manifest(
    populated_trust_dir: Path,
    manifest_entries,  # noqa: ANN001 — fixture from conftest
    monkeypatch,
    capsys,
) -> None:
    """AC #4: each printed fingerprint matches the manifest's stored value.

    The command's output need not be machine-readable in any specific
    format, but every manifest fingerprint must appear at least once and
    be paired with its (role, key_type). We assert containment so the
    CLI can pick its own line shape.
    """
    app = _import_app()
    monkeypatch.chdir(populated_trust_dir)

    rc = _run_app(app, ["fingerprints"])
    out = capsys.readouterr().out

    if rc not in (None, 0):
        pytest.fail(f"trust fingerprints exited {rc!r}")

    for entry in manifest_entries:
        assert entry.fingerprint in out, (
            f"fingerprint for {entry.name}/{entry.key_type} ({entry.fingerprint}) "
            f"missing from output:\n{out}"
        )


def test_fingerprints_deterministic_order(
    populated_trust_dir: Path,
    monkeypatch,
    capsys,
) -> None:
    """Two consecutive runs produce byte-identical output.

    Catches accidental dependence on dict iteration order or local time
    formatting — sorted output is the cheapest way to keep the runbook
    diffable.
    """
    app = _import_app()
    monkeypatch.chdir(populated_trust_dir)

    _run_app(app, ["fingerprints"])
    first = capsys.readouterr().out

    _run_app(app, ["fingerprints"])
    second = capsys.readouterr().out

    assert first == second, "fingerprints output is not deterministic"


def test_fingerprints_fails_when_trust_absent(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    """No .trust/ directory: nonzero exit and a clear stderr message.

    Mirrors AC #3's requirement on the renderer: the trust subcommands
    must not fall back to silent generation.
    """
    app = _import_app()
    monkeypatch.chdir(tmp_path)

    rc = _run_app(app, ["fingerprints"])

    captured = capsys.readouterr()
    assert rc not in (0, None), "command must exit nonzero when .trust/ is absent"
    assert captured.err.strip(), "expected a clear error on stderr"
