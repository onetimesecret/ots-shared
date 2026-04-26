# packages/ots-shared/tests/trust/test_cli_prune_flock.py

"""Tests for the flock around ``trust prune`` (PR #61 follow-up, Item 5).

Pruning mutates ``.trust/`` (rmtree) and the manifest (rewrite). It
must hold the same flock that ``init`` uses so a concurrent ``init``
cannot regenerate while ``prune`` is half-done. The lock target is the
operator-checkout root (not ``.trust/`` itself) — see
``ots_shared.trust.init_step._trust_flock`` for the rmtree-vs-inode
reasoning.
"""

from __future__ import annotations

import contextlib
from pathlib import Path

import pytest
from ots_shared.trust.manifest import Manifest


def _run_app(args: list[str]) -> int | None:
    from ots_shared.trust.cli import app

    with pytest.raises(SystemExit) as exc_info:
        app(args)
    return exc_info.value.code


# ---- F2: prune calls trust_flock with the checkout root ------------------


def test_prune_calls_trust_flock_with_checkout_root(
    populated_trust_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``prune`` must invoke ``trust_flock(checkout_root)`` exactly once.

    Patch the symbol the prune module looks up at call time and record
    each invocation's argument. The lock target must be the checkout
    directory (where ``.otsinfra.yaml`` lives), NOT ``.trust/`` — locking
    on ``.trust/`` would break under ``--force`` rmtree because the
    inode the lock fd holds gets unlinked.
    """
    captured: list[Path] = []

    @contextlib.contextmanager
    def _spy_flock(target: Path):
        captured.append(Path(target))
        yield

    # Patch where prune imports the symbol from. cli.py does
    # ``from ots_shared.trust import trust_flock``, so the bound name
    # lives at ``ots_shared.trust.cli.trust_flock``.
    import ots_shared.trust.cli as trust_cli

    monkeypatch.setattr(trust_cli, "trust_flock", _spy_flock)

    _set_marker(populated_trust_dir, "db")  # web undeclared
    monkeypatch.chdir(populated_trust_dir)

    rc = _run_app(["prune", "web", "--yes"])
    if rc not in (None, 0):
        pytest.fail(f"prune web --yes exited {rc!r}")

    assert captured, "prune must call trust_flock at least once"
    # All captured targets must equal the checkout root, not the .trust/
    # subdir. Normalise to absolute paths for comparison.
    expected = populated_trust_dir.resolve()
    forbidden = (populated_trust_dir / ".trust").resolve()
    for target in captured:
        resolved = target.resolve()
        assert resolved == expected, (
            f"trust_flock target must be the checkout root ({expected}); got {resolved}"
        )
        assert resolved != forbidden, (
            "trust_flock target must NOT be .trust/ — see init_step "
            "docstring for the rmtree-inode rationale"
        )


# ---- F1: lock is held during prune (real flock, separate process) -------


def test_prune_holds_exclusive_lock_during_run(
    populated_trust_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Concurrent ``trust_flock(checkout)`` cannot acquire while prune holds it.

    Approach: instrument prune so that mid-run we attempt a non-blocking
    ``fcntl.LOCK_EX | LOCK_NB`` from a *child process*. ``flock`` is per
    open-file-description, so a child process gets its own fd and the
    lock contention is real (vs. a same-process attempt which would
    succeed on Linux/macOS because the kernel grants re-entrant flock
    to the same inode/fd in the same process).
    """
    import fcntl

    # The instrumentation: monkey-patch ``shutil.rmtree`` (called inside
    # the lock by prune) to fork a child that tries to acquire the same
    # flock non-blocking and reports back. We pause briefly so the kernel
    # has a clean window to grant or deny. The child writes its result
    # to a tmp file the parent reads after prune exits.
    import shutil
    import subprocess
    import sys

    original_rmtree = shutil.rmtree
    contention_result_path = populated_trust_dir / "_contention.txt"

    def _rmtree_with_contention_probe(path, *args, **kwargs):
        # Spawn a child to probe the lock. Use a one-shot Python -c so
        # the child has its own flock fd.
        probe_script = (
            "import fcntl, os, sys; "
            f"fd = os.open({str(populated_trust_dir.resolve())!r}, os.O_RDONLY | os.O_DIRECTORY); "
            "rc = 0\n"
            "try:\n"
            "    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
            "except BlockingIOError:\n"
            "    rc = 11\n"
            "except OSError as e:\n"
            "    rc = e.errno or 1\n"
            f"open({str(contention_result_path)!r}, 'w').write(str(rc))\n"
            "sys.exit(0)\n"
        )
        subprocess.run([sys.executable, "-c", probe_script], check=False, timeout=10)
        return original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr("shutil.rmtree", _rmtree_with_contention_probe)

    _set_marker(populated_trust_dir, "db")  # web undeclared
    monkeypatch.chdir(populated_trust_dir)

    rc = _run_app(["prune", "web", "--yes"])
    if rc not in (None, 0):
        pytest.fail(f"prune web --yes exited {rc!r}")

    # If prune properly held an exclusive flock on the checkout, the
    # child's non-blocking acquisition should have failed with EWOULDBLOCK
    # (errno 11 on Linux, or EAGAIN — fcntl raises BlockingIOError either
    # way). If it succeeded (rc == 0), the lock was not held.
    assert contention_result_path.exists(), (
        "contention probe never ran — rmtree was not called inside prune"
    )
    probe_rc = contention_result_path.read_text().strip()
    # 0 = the child acquired the lock = prune did NOT hold it.
    # Any non-zero (typically 11/EWOULDBLOCK) = contention detected = lock held.
    assert probe_rc != "0", (
        f"trust_flock did not block a concurrent acquisition during prune; "
        f"probe child reported rc={probe_rc!r} (expected non-zero / EWOULDBLOCK)"
    )
    # Document the exact errno for debug visibility — EAGAIN/EWOULDBLOCK
    # are the only sane outcomes here. Anything else indicates the test
    # itself broke.
    _ = fcntl  # silence unused if path skipped


# ---- helpers --------------------------------------------------------------


def _set_marker(checkout: Path, *roles: str) -> None:
    lines = ["environment: test-fixture", "created: '2026-04-25'"]
    if roles:
        lines.append("hosts:")
        for role in roles:
            lines.append(f"  {role}:")
            lines.append(f"    private_ip_address: 10.0.0.{1 + roles.index(role)}")
    (checkout / ".otsinfra.yaml").write_text("\n".join(lines) + "\n")


# Keep the manifest import live so a future test that wants to assert
# post-prune manifest state has it available without re-importing.
_ = Manifest
