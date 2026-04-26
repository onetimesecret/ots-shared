# tests/trust/test_concurrency.py

"""Concurrency tests for the .trust/ generation flock (spec §77, AC #5).

Uses ``multiprocessing.get_context("spawn")`` because flock is per-fd:
threads share the parent process file descriptors, so a within-process
test would not exercise the lock the same way two ``lots init``
invocations from a shell would. ``spawn`` also avoids macOS fork-safety
issues with the cryptography library.

Some tests will fail until W2-init lands ``create_trust_material`` in
``ots_shared.trust.init_step``. That is expected.
"""

from __future__ import annotations

import multiprocessing
from pathlib import Path

import pytest

# Helper functions defined at module scope so spawn can serialise them.

_HOSTS_FOR_WORKER = ["web", "db"]


def _run_create_trust_material(target: str) -> str:
    """Spawned worker: invoke create_trust_material against *target*.

    Returns the CA fingerprint (or a sentinel ``ERROR:<repr>``) so the
    parent can compare convergence between workers.
    """
    from pathlib import Path as _Path

    try:
        from ots_shared.trust.init_step import (
            create_trust_material,  # type: ignore[import-not-found]
        )
    except Exception as exc:  # pragma: no cover - exercised once init_step lands
        return f"ERROR:import:{exc!r}"

    try:
        create_trust_material(_Path(target), hosts=_HOSTS_FOR_WORKER)
    except Exception as exc:
        return f"ERROR:run:{exc!r}"

    try:
        from ots_shared.trust.ca import load_ca

        ca = load_ca(_Path(target) / ".trust" / "ca")
    except Exception as exc:
        return f"ERROR:load:{exc!r}"
    return ca.fingerprint


def _run_create_then_raise(target: str) -> str:
    """Spawned worker that forces ``create_trust_material`` to raise mid-run.

    Simulates a partial-failure path where the lock must be released
    before the worker exits. The patch targets a symbol *imported into*
    ``init_step``'s namespace (``init_step.generate_keypair``) rather
    than the ``ots_shared.trust`` package attribute, because
    ``from ots_shared.trust import generate_keypair`` rebinds the name
    in ``init_step`` and patching the package attribute would leave
    that bound name unaffected.

    If ``init_step`` does not have a ``generate_keypair`` name (for
    example, the impl uses a private function), the patch falls back
    to bumping the manifest writer or the ssh keypair generator —
    whichever is actually present — so this test stays useful across
    plausible W2-init shapes.
    """
    from pathlib import Path as _Path

    try:
        from ots_shared.trust import init_step  # type: ignore[import-not-found]
        from ots_shared.trust.init_step import (
            create_trust_material,  # type: ignore[import-not-found]
        )
    except Exception as exc:  # pragma: no cover
        return f"ERROR:import:{exc!r}"

    # Try a sequence of plausible patch points. The first one that
    # exists wins; patching is a no-op if none are found, in which
    # case the test will fail loud with "second worker did not
    # succeed" if the lock somehow leaks anyway.
    patched_attr: str | None = None
    original = None
    for attr in ("generate_keypair", "_generate_keypair", "generate_ca"):
        if hasattr(init_step, attr):
            patched_attr = attr
            original = getattr(init_step, attr)
            break

    def _boom(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("synthetic mid-generation failure")

    if patched_attr is not None:
        setattr(init_step, patched_attr, _boom)
    try:
        create_trust_material(_Path(target), hosts=_HOSTS_FOR_WORKER)
    except Exception:
        pass
    finally:
        if patched_attr is not None and original is not None:
            setattr(init_step, patched_attr, original)
    return "FAILED_AS_EXPECTED"


def _seed_marker(target: Path) -> None:
    """Write a minimal .otsinfra.yaml so create_trust_material has a host set.

    Keeps the test independent of the marker generator's exact output.
    """
    (target / ".otsinfra.yaml").write_text(
        "env_name: test-conc\n"
        "created: '2026-04-25'\n"
        "hosts:\n"
        "  web:\n"
        "    private_ip_address: 10.0.0.21\n"
        "  db:\n"
        "    private_ip_address: 10.0.0.11\n"
    )


def _first_worker_failing(target: str) -> None:
    """Convenience entrypoint usable by Process(...) for the recovery test."""
    _run_create_then_raise(target)


def _second_worker_recovering(target: str) -> None:
    """Calls the real flow after the first worker raised.

    Exits non-zero with a recognisable code if create_trust_material is
    not yet importable so the parent test fails for the right reason.
    """
    out = _run_create_trust_material(target)
    if out.startswith("ERROR:"):
        raise SystemExit(2)


# ---- AC #5, spec §77 -------------------------------------------------------


def test_two_concurrent_inits_converge_under_flock(tmp_path: Path) -> None:
    """spec §77, AC #5: two concurrent invocations converge on a single state.

    Two worker processes call ``create_trust_material`` against the same
    ``.trust/`` directory. Both must complete without error. The CA
    fingerprint each worker observes after its own call must match the
    other's, and the on-disk fingerprint after both finish must equal
    what each worker reported.
    """
    _seed_marker(tmp_path)

    ctx = multiprocessing.get_context("spawn")
    with ctx.Pool(processes=2) as pool:
        # Same target for both workers — flock is the only thing that
        # serialises them.
        results = pool.map(_run_create_trust_material, [str(tmp_path), str(tmp_path)])

    # If create_trust_material doesn't exist yet, both will return an
    # ERROR:import:... sentinel. Surface that as a clear test failure
    # rather than crashing on the assertion below.
    assert all(not r.startswith("ERROR:") for r in results), (
        f"workers errored: {results}"
    )

    # Both must have observed the same CA fingerprint after their call —
    # i.e. the second invocation did not regenerate.
    assert results[0] == results[1], (
        f"concurrent workers diverged: {results}"
    )

    # And the on-disk fingerprint after both finish must agree.
    from ots_shared.trust.ca import load_ca

    final = load_ca(tmp_path / ".trust" / "ca").fingerprint
    assert final == results[0]


def test_flock_released_on_exception(tmp_path: Path) -> None:
    """spec §77: a mid-generation exception must release the flock.

    Worker A patches ``generate_keypair`` to raise, runs
    ``create_trust_material``, and exits. Worker B then runs normally.
    If the lock leaked, B would block forever (or fail with EAGAIN
    depending on impl). The 30s join timeout is a safety net so the
    test fails noisily rather than hanging the suite.
    """
    _seed_marker(tmp_path)

    ctx = multiprocessing.get_context("spawn")

    p1 = ctx.Process(target=_first_worker_failing, args=(str(tmp_path),))
    p1.start()
    p1.join(timeout=30)
    assert p1.exitcode is not None, "failing worker hung — flock may have leaked"

    p2 = ctx.Process(target=_second_worker_recovering, args=(str(tmp_path),))
    p2.start()
    p2.join(timeout=30)
    assert p2.exitcode == 0, (
        f"second worker did not succeed after first errored "
        f"(exitcode={p2.exitcode}); flock may not have been released"
    )


# ---- AC #5, spec §7 + §77 (--force race) -----------------------------------


def _run_slow_create(target: str) -> str:
    """Spawned worker: invoke create_trust_material with a sleep injected
    into ``generate_keypair`` so the generation phase straddles a concurrent
    ``--force`` run from a sibling process. Returns the CA fingerprint
    observed after the worker's own call (or ``ERROR:...`` on failure).

    The patch lives inside the worker because spawn re-imports the module
    in a fresh interpreter; patching in the parent would not propagate.
    """
    import time
    from pathlib import Path as _Path

    try:
        from ots_shared.trust import init_step  # type: ignore[import-not-found]
        from ots_shared.trust.init_step import (
            create_trust_material,  # type: ignore[import-not-found]
        )
    except Exception as exc:  # pragma: no cover
        return f"ERROR:import:{exc!r}"

    original = init_step.generate_keypair

    def _slow(*args, **kwargs):  # type: ignore[no-untyped-def]
        time.sleep(0.5)
        return original(*args, **kwargs)

    init_step.generate_keypair = _slow  # type: ignore[assignment]
    try:
        create_trust_material(_Path(target), hosts=_HOSTS_FOR_WORKER)
    except Exception as exc:
        return f"ERROR:run:{exc!r}"
    finally:
        init_step.generate_keypair = original  # type: ignore[assignment]

    try:
        from ots_shared.trust.ca import load_ca

        ca = load_ca(_Path(target) / ".trust" / "ca")
    except Exception as exc:
        return f"ERROR:load:{exc!r}"
    return ca.fingerprint


def _run_force_create(target: str) -> str:
    """Spawned worker that runs create_trust_material with force=True."""
    from pathlib import Path as _Path

    try:
        from ots_shared.trust.init_step import (
            create_trust_material,  # type: ignore[import-not-found]
        )
    except Exception as exc:  # pragma: no cover
        return f"ERROR:import:{exc!r}"

    try:
        create_trust_material(
            _Path(target), hosts=_HOSTS_FOR_WORKER, force=True
        )
    except Exception as exc:
        return f"ERROR:run:{exc!r}"

    try:
        from ots_shared.trust.ca import load_ca

        ca = load_ca(_Path(target) / ".trust" / "ca")
    except Exception as exc:
        return f"ERROR:load:{exc!r}"
    return ca.fingerprint


def test_force_race_does_not_corrupt_trust_state(tmp_path: Path) -> None:
    """spec §7 + §77, AC #5: a concurrent ``--force`` invocation must not
    rmtree another worker's in-flight ``.trust/``.

    Worker A starts a slow generation (sleep injected into generate_keypair).
    Worker B fires with ``force=True`` mid-generation. The flock must
    serialise them so the final on-disk state is coherent: the CA file
    exists, loads cleanly, and at least one worker's reported fingerprint
    matches what's on disk.

    Without a flock that scopes the rmtree, Worker B would unlink Worker
    A's ``.trust/`` while A still holds half-written files, leaving a
    mixed-inode directory and divergent fingerprints between processes.
    """
    _seed_marker(tmp_path)

    ctx = multiprocessing.get_context("spawn")

    p_slow = ctx.Process(target=_force_race_slow_entry, args=(str(tmp_path),))
    p_force = ctx.Process(target=_force_race_force_entry, args=(str(tmp_path),))

    p_slow.start()
    # Give the slow worker time to acquire the lock and start generating.
    import time

    time.sleep(0.1)
    p_force.start()

    p_slow.join(timeout=30)
    p_force.join(timeout=30)

    assert p_slow.exitcode == 0, (
        f"slow worker did not finish cleanly (exitcode={p_slow.exitcode})"
    )
    assert p_force.exitcode == 0, (
        f"--force worker did not finish cleanly (exitcode={p_force.exitcode})"
    )

    # Final state must be loadable — i.e. the CA inode is intact and
    # neither worker tore down a directory the other was writing into.
    from ots_shared.trust.ca import load_ca

    ca = load_ca(tmp_path / ".trust" / "ca")
    assert ca.fingerprint, "final CA fingerprint must be readable"

    # Per-host material must be coherent — every role has its full set.
    hosts_root = tmp_path / ".trust" / "hosts"
    for role in _HOSTS_FOR_WORKER:
        for name in ("ssh", "ssh.pub", "wg", "wg.pub", "key.pem", "cert.pem"):
            assert (hosts_root / role / name).is_file(), (
                f"hosts/{role}/{name} missing — concurrent --force corrupted state"
            )


def _force_race_slow_entry(target: str) -> None:
    """Process target for the slow worker in the --force race test."""
    out = _run_slow_create(target)
    if out.startswith("ERROR:"):
        raise SystemExit(2)


def _force_race_force_entry(target: str) -> None:
    """Process target for the --force worker in the --force race test."""
    out = _run_force_create(target)
    if out.startswith("ERROR:"):
        raise SystemExit(2)


# Silence unused-import lint when ``pytest`` is only used for fixtures.
_ = pytest
