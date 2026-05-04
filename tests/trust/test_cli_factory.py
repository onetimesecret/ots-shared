# tests/trust/test_cli_factory.py

"""Tests for ``ots_shared.trust.cli.make_trust_app`` (PR #2 Contract 4).

The trust subapp is parameterized with the parent CLI's tool name so
operator-facing error wording references the right command (e.g.
``lots init`` vs ``pots init``). The factory keeps the trust commands
themselves tool-agnostic — only the user-facing strings change.

Wording contract (matches the closure in ``cli.py``):

* with a tool name supplied:
  ``Error: <path> not found; run `<tool_name> init` to materialize trust material.``
* with no tool name (default ``app``):
  ``Error: <path> not found; run the init command for your environment
  to materialize trust material.``

The simplest way to exercise the closure is to invoke any subcommand
inside a temp dir that has ``otsinfra.yaml`` (so ``resolve_trust_dir``
succeeds) but no ``.trust/`` directory (so the existence check fails
and the closure prints).
"""

from __future__ import annotations

from pathlib import Path

import cyclopts
import pytest

from ots_shared.trust.cli import app as default_app
from ots_shared.trust.cli import make_trust_app


def _run_app(app: cyclopts.App, args: list[str]) -> object:
    """Invoke a Cyclopts app, returning its exit code (always raises SystemExit).

    Return type is ``object`` because ``SystemExit.code`` is typed as
    ``_ExitCode`` (str | int | None) — callers compare against ``0`` /
    ``None`` directly without assuming a narrower shape.
    """
    with pytest.raises(SystemExit) as exc_info:
        app(args)
    return exc_info.value.code


def _seed_marker_only(tmp_path: Path) -> None:
    """Write ``otsinfra.yaml`` so ``resolve_trust_dir`` succeeds but ``.trust/`` is absent."""
    (tmp_path / "otsinfra.yaml").write_text("env_name: test-cli-factory\ncreated: '2026-04-25'\n")


# ---- factory return type --------------------------------------------------


def test_make_trust_app_returns_cyclopts_app() -> None:
    """The factory returns a fresh ``cyclopts.App`` instance per call.

    Each parent CLI builds its own subapp; sharing an instance would
    mean a tool-name change in one CLI bleeds into another's error
    messages.
    """
    a = make_trust_app("lots")
    b = make_trust_app("pots")
    assert isinstance(a, cyclopts.App)
    assert isinstance(b, cyclopts.App)
    assert a is not b, "factory must produce a distinct App per call"


# ---- with tool_name="lots" ------------------------------------------------


def test_missing_trust_dir_message_mentions_lots_init(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``make_trust_app("lots")`` → stderr says ``run `lots init`...``.

    Pins Contract 4: parent CLIs supplying their own name get that name
    interpolated into the operator hint, backticked so it stands out as
    a literal command.
    """
    app = make_trust_app("lots")
    _seed_marker_only(tmp_path)
    monkeypatch.chdir(tmp_path)

    rc = _run_app(app, ["list"])

    captured = capsys.readouterr()
    assert rc not in (0, None), "missing .trust/ must exit nonzero"
    assert "`lots init`" in captured.err, (
        f"stderr must contain backticked `lots init`; got:\n{captured.err}"
    )


def test_missing_trust_dir_message_mentions_pots_init(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``make_trust_app("pots")`` → stderr says ``run `pots init`...``.

    Mirrors the lots case; ensures the factory actually consumes the
    parameter rather than ignoring it after the first call.
    """
    app = make_trust_app("pots")
    _seed_marker_only(tmp_path)
    monkeypatch.chdir(tmp_path)

    rc = _run_app(app, ["list"])

    captured = capsys.readouterr()
    assert rc not in (0, None)
    assert "`pots init`" in captured.err, (
        f"stderr must contain backticked `pots init`; got:\n{captured.err}"
    )


# ---- without tool_name (factory default) ----------------------------------


def test_missing_trust_dir_message_no_tool_name_uses_generic_wording(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``make_trust_app()`` (no tool name) → generic wording, no backticked tool.

    The generic hint is what surfaces when the trust subapp is invoked
    standalone (no parent CLI). It must NOT reference any specific tool
    name — and explicitly must not include backtick-wrapped ``init``
    text, since that would be a misleading literal command for the
    operator to copy/paste.
    """
    app = make_trust_app()
    _seed_marker_only(tmp_path)
    monkeypatch.chdir(tmp_path)

    rc = _run_app(app, ["list"])

    captured = capsys.readouterr()
    assert rc not in (0, None)
    assert "run the init command for your environment" in captured.err, (
        f"stderr must use the generic init wording; got:\n{captured.err}"
    )
    # Negative assertion: no backticked tool-name init phrase. We look
    # for the leading backtick + ``init`` literal that the parameterised
    # form emits (e.g. `lots init`, `pots init`).
    assert "init`" not in captured.err, (
        f"generic wording must not embed a backticked tool-name init; got:\n{captured.err}"
    )


def test_module_level_app_uses_generic_wording(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Module-level ``app = make_trust_app()`` matches the no-arg factory result.

    Pins that the convenience module-level export is genuinely the
    no-tool-name app — not a separate construction with different
    wording.
    """
    _seed_marker_only(tmp_path)
    monkeypatch.chdir(tmp_path)

    rc = _run_app(default_app, ["list"])

    captured = capsys.readouterr()
    assert rc not in (0, None)
    assert "run the init command for your environment" in captured.err, (
        f"module-level app must emit the generic init wording; got:\n{captured.err}"
    )
    assert "init`" not in captured.err
