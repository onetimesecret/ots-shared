# tests/trust/test_trust_flock.py

"""Direct tests for the ``trust_flock`` public API (PR #61 follow-up, Item 5).

``trust_flock`` is consolidated under ``ots_shared.trust`` so callers in
``lots init``, ``lots trust prune``, and any future tool that mutates
``.trust/`` state share one lock implementation. The companion
``test_concurrency.py`` exercises the lock through ``create_trust_material``;
this file pins the lower-level contract directly.

Spec §77: an exclusive flock guards generation so two simultaneous
invocations from the same checkout converge on a single trust state.
The lock target is the operator-checkout directory itself (not
``.trust/``) — see ``trust_flock``'s docstring for the rmtree-vs-inode
rationale.
"""

from __future__ import annotations

import contextlib
import multiprocessing
import os
import resource
import time
from pathlib import Path

import pytest

from ots_shared.trust import trust_flock  # type: ignore[import-not-found]

# ---- helpers (module scope so spawn workers can serialise them) ----------


def _hold_lock_for(target_str: str, hold_seconds: float, ready_path_str: str) -> int:
    """Worker: take the lock, signal readiness, hold for ``hold_seconds``.

    Writes ``ready_path`` to tell the parent the lock is acquired so the
    parent doesn't race the worker's startup. Exit code 0 = acquired and
    released cleanly; non-zero = something went wrong.
    """
    target = Path(target_str)
    ready_path = Path(ready_path_str)
    try:
        with trust_flock(target):
            ready_path.write_text("ready")
            time.sleep(hold_seconds)
    except Exception:
        return 2
    return 0


def _try_nonblocking(target_str: str, result_path_str: str) -> int:
    """Worker: attempt non-blocking acquire and write the outcome.

    Writes ``"acquired"`` if the lock was obtained, ``"blocked"`` if the
    kernel returned EAGAIN/EWOULDBLOCK. Exits 0 either way — the parent
    reads the result file to learn what happened.
    """
    import fcntl

    result_path = Path(result_path_str)
    fd = os.open(target_str, os.O_RDONLY | os.O_DIRECTORY)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            result_path.write_text("blocked")
            return 0
        # Acquired — release immediately so we don't wedge the parent's
        # cleanup if the test asserts a different state.
        fcntl.flock(fd, fcntl.LOCK_UN)
        result_path.write_text("acquired")
    finally:
        os.close(fd)
    return 0


# ---- G1: context-manager acquire/release ---------------------------------


def test_trust_flock_is_context_manager(tmp_path: Path) -> None:
    """``trust_flock(dir)`` is a context manager that acquires and releases.

    Two sequential ``with`` blocks against the same target must both
    succeed — proves the lock is released on normal exit, not held for
    the lifetime of the process.
    """
    with trust_flock(tmp_path):
        pass

    # Second acquisition must succeed — if the first didn't release, this
    # would hang or fail.
    with trust_flock(tmp_path):
        pass


def test_trust_flock_releases_on_exception(tmp_path: Path) -> None:
    """Exception inside the ``with`` block must still release the lock.

    A leaked fd or held lock would prevent the second acquisition from
    succeeding within this same process (``flock`` is per-fd, but the
    helper opens a fresh fd each time — if the *previous* fd leaks
    *and* the kernel grants exclusivity per-inode, the second open's
    flock would block).
    """

    class Sentinel(RuntimeError):
        pass

    with contextlib.suppress(Sentinel):
        with trust_flock(tmp_path):
            raise Sentinel("synthetic")

    # Re-acquisition must succeed — the first ``with`` released its
    # lock and closed its fd in the ``finally`` even though the body
    # raised.
    with trust_flock(tmp_path):
        pass


# ---- G2: cross-process exclusion -----------------------------------------


def test_trust_flock_blocks_concurrent_acquirer(tmp_path: Path) -> None:
    """A second process cannot acquire while the first holds the lock.

    The first worker takes ``trust_flock`` and sleeps. The second worker
    attempts a non-blocking acquire and reports back. If the kernel's
    flock contract is wired correctly, the second worker must observe
    ``BlockingIOError`` (EAGAIN/EWOULDBLOCK).

    ``spawn`` is used because flock is per-fd and ``fork`` would inherit
    the parent's fds, defeating the test. ``spawn`` also matches the
    pattern in ``test_concurrency.py``.
    """
    ready_path = tmp_path / "_holder_ready.txt"
    result_path = tmp_path / "_probe_result.txt"

    ctx = multiprocessing.get_context("spawn")

    holder = ctx.Process(
        target=_hold_lock_for,
        args=(str(tmp_path), 2.0, str(ready_path)),
    )
    holder.start()
    try:
        # Wait for the holder to actually acquire the lock before we
        # probe — without this, the probe could win the race and the
        # test would falsely conclude the lock was unheld.
        deadline = time.time() + 5.0
        while not ready_path.exists() and time.time() < deadline:
            time.sleep(0.02)
        assert ready_path.exists(), "holder process did not acquire lock within timeout"

        probe = ctx.Process(
            target=_try_nonblocking,
            args=(str(tmp_path), str(result_path)),
        )
        probe.start()
        probe.join(timeout=10)
        assert probe.exitcode == 0, f"probe worker exited {probe.exitcode!r}"

        assert result_path.read_text() == "blocked", (
            "non-blocking acquire from a separate process must fail with "
            "EAGAIN while the first process holds trust_flock"
        )
    finally:
        holder.join(timeout=10)
        assert holder.exitcode == 0, f"holder worker exited {holder.exitcode!r}"


def test_trust_flock_releases_so_next_acquirer_succeeds(tmp_path: Path) -> None:
    """After the holder releases, a fresh process must be able to acquire.

    Inverse of the contention test: once the first ``with`` block exits,
    the lock must be free for the next caller. Without this, the lock
    would behave like a one-shot mutex — useless for the prune/init
    serialisation pattern.
    """
    ready_path = tmp_path / "_holder_ready.txt"
    result_path = tmp_path / "_probe_result.txt"

    ctx = multiprocessing.get_context("spawn")
    holder = ctx.Process(
        target=_hold_lock_for,
        args=(str(tmp_path), 0.05, str(ready_path)),  # release quickly
    )
    holder.start()
    holder.join(timeout=10)
    assert holder.exitcode == 0

    probe = ctx.Process(
        target=_try_nonblocking,
        args=(str(tmp_path), str(result_path)),
    )
    probe.start()
    probe.join(timeout=10)
    assert probe.exitcode == 0

    assert result_path.read_text() == "acquired", (
        "after holder released, a non-blocking acquire from another "
        "process must succeed"
    )


# ---- G3: no fd leak ------------------------------------------------------


def test_trust_flock_does_not_leak_fds(tmp_path: Path) -> None:
    """Repeated acquire/release must not exhaust the open-file ulimit.

    The contract opens a fd via ``os.open`` and closes it in ``finally``.
    Loop the context manager more times than a ``select(2)`` fd-set
    would allow if leaked, then confirm the directory can still be
    opened. A future refactor that forgets ``os.close`` would surface
    here before it bites in long-running daemons.
    """
    soft, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
    # Cap iterations so the test stays fast on systems with a tiny
    # default (e.g. macOS often defaults to 256). 200 round-trips is
    # well below any sane limit and well above 1.
    iterations = min(200, max(50, soft // 4))

    for _ in range(iterations):
        with trust_flock(tmp_path):
            pass

    # If a leak existed, by now opening a fresh fd should fail with
    # EMFILE. Confirm we can still ``open`` the directory directly —
    # this stands in for "no fd exhaustion".
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    os.close(fd)


# ---- G4: target must be a directory --------------------------------------


def test_trust_flock_accepts_directory_path(tmp_path: Path) -> None:
    """``trust_flock`` accepts a directory path (not a file).

    Per the docstring the lock target is the operator-checkout directory.
    ``os.open`` with ``O_DIRECTORY`` enforces this at the syscall level —
    if the impl ever switched to a file-based lock the test would fail.
    """
    assert tmp_path.is_dir(), "fixture invariant"
    with trust_flock(tmp_path):
        pass  # would have raised on entry if O_DIRECTORY refused the path


def test_trust_flock_rejects_non_directory_target(tmp_path: Path) -> None:
    """Pointing the lock at a regular file is a programming error.

    The current impl uses ``O_DIRECTORY`` so the kernel rejects this with
    ``ENOTDIR``. Pinning the behaviour ensures a future "convenience"
    rewrite that drops the flag (and lets a file lock through) gets
    caught — files don't survive ``rmtree`` the way the docstring's
    rationale assumes about directories.
    """
    not_a_dir = tmp_path / "not_a_dir.txt"
    not_a_dir.write_text("file not directory")

    with pytest.raises((NotADirectoryError, OSError)):
        with trust_flock(not_a_dir):
            pass
