# tests/trust/test_init_partial_ca_healing.py

"""CA-manifest reconciliation tests for ``create_trust_material``.

Pins PR #2 Contract 1: the orchestrator no longer uses file-presence
to decide whether to upsert the CA manifest entry. Instead, after
loading the manifest under the flock it compares
``manifest.get("ca", "ca")`` against the live ``ca.fingerprint``:

* missing entry           → upsert + print ``f"ca {fingerprint}"``
* mismatched fingerprint  → upsert + print ``f"ca {fingerprint}"``
* matching fingerprint    → leave manifest alone, print nothing

The previous behaviour (skip the CA upsert whenever ``ca.crt`` was on
disk) silently left the manifest stale when the on-disk identity was
regenerated mid-recovery (e.g. serial counter wiped → ``generate_ca``
mints a new identity but the old manifest entry survives).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ots_shared.trust import make_manifest_entry
from ots_shared.trust.ca import generate_ca, load_ca
from ots_shared.trust.init_step import create_trust_material
from ots_shared.trust.manifest import Manifest


def _make_marker(target: Path) -> None:
    """Write a minimal ``otsinfra.yaml`` declaring two host roles."""
    (target / "otsinfra.yaml").write_text(
        "env_name: test-ca-healing\n"
        "created: '2026-04-25'\n"
        "hosts:\n"
        "  web:\n"
        "    private_ip_address: 10.0.0.21\n"
        "  db:\n"
        "    private_ip_address: 10.0.0.11\n"
    )


def _seed_trust_root(target: Path) -> Path:
    """Create the ``.trust/`` root with the expected mode."""
    trust = target / ".trust"
    trust.mkdir(mode=0o700)
    return trust


# ---- scenario 1: partial CA on disk (serial missing) -----------------------


def test_partial_ca_dir_heals_and_records_new_fingerprint(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Partial CA dir (serial deleted) → fresh CA minted, manifest carries new fp.

    Setup: a real CA exists on disk, then we delete the ``serial`` file.
    ``generate_ca`` regenerates the entire CA when any of {ca.crt, ca.key,
    serial} is missing (its loader requires all three). The orchestrator
    must notice the manifest's CA fingerprint no longer matches the
    freshly-minted CA's fingerprint and upsert + print accordingly.
    """
    _make_marker(tmp_path)
    trust = _seed_trust_root(tmp_path)

    # Build a real CA, capture its fingerprint, then break the dir by
    # deleting the serial file. ``generate_ca`` will mint a fresh identity
    # on the next call.
    stale_ca = generate_ca(trust / "ca")
    stale_fp = stale_ca.fingerprint
    stale_ca.serial_path.unlink()

    # Pre-populate the manifest with an entry pointing at the now-stale
    # fingerprint to prove the reconciliation overwrites it.
    manifest_path = trust / "manifest.yaml"
    pre = Manifest()
    pre.upsert(
        make_manifest_entry(
            name="ca",
            key_type="ca",
            fingerprint=stale_fp,
            serial=1,
        )
    )
    pre.save(manifest_path)

    create_trust_material(tmp_path, hosts=["web", "db"])

    # CA on disk after the run is the freshly-minted one.
    fresh_fp = load_ca(trust / "ca").fingerprint
    assert fresh_fp != stale_fp, (
        "fixture invariant: deleting the serial file should force generate_ca "
        "to mint a new identity"
    )

    # Manifest's CA entry now records the fresh fingerprint.
    after = Manifest.load(manifest_path)
    ca_entry = after.get("ca", "ca")
    assert ca_entry is not None, "manifest must carry a CA entry after init"
    assert ca_entry.fingerprint == fresh_fp, (
        f"manifest CA fingerprint {ca_entry.fingerprint!r} did not heal to "
        f"fresh on-disk fingerprint {fresh_fp!r}"
    )

    # Stdout includes the canonical ``ca <fingerprint>`` line for the
    # newly-recorded fingerprint.
    out = capsys.readouterr().out
    assert f"ca {fresh_fp}" in out, f"expected `ca {fresh_fp}` in stdout; got:\n{out}"


# ---- scenario 2: stale fingerprint in an otherwise-healthy manifest --------


def test_stale_manifest_fingerprint_is_updated_to_real_ca(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Healthy CA on disk + manifest with wrong fingerprint → manifest healed.

    Models the exact race the contract change targets: the manifest was
    written against an earlier CA identity (e.g. a destructive ``--force``
    run on another worktree) and never got reconciled. A presence-only
    check would skip the upsert because ``ca.crt`` exists; the
    fingerprint-comparison check catches the drift.
    """
    _make_marker(tmp_path)
    trust = _seed_trust_root(tmp_path)

    real_ca = generate_ca(trust / "ca")
    real_fp = real_ca.fingerprint

    manifest_path = trust / "manifest.yaml"
    pre = Manifest()
    pre.upsert(
        make_manifest_entry(
            name="ca",
            key_type="ca",
            fingerprint="sha256:STALE",
            serial=1,
        )
    )
    pre.save(manifest_path)

    create_trust_material(tmp_path, hosts=["web", "db"])

    # On-disk CA unchanged (idempotent).
    assert load_ca(trust / "ca").fingerprint == real_fp

    after = Manifest.load(manifest_path)
    ca_entry = after.get("ca", "ca")
    assert ca_entry is not None
    assert ca_entry.fingerprint == real_fp, (
        f"manifest CA fingerprint not healed; still {ca_entry.fingerprint!r}"
    )

    out = capsys.readouterr().out
    assert f"ca {real_fp}" in out, f"stale-fingerprint heal must print `ca {real_fp}`; got:\n{out}"


# ---- scenario 3: manifest already in sync — quiet no-op --------------------


def test_in_sync_manifest_is_unchanged_and_quiet(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Manifest already records the live CA fingerprint → no upsert, no print.

    Asserts the negative case: the orchestrator must not re-upsert or
    re-emit the ``ca`` line on every run. Operators rely on the printed
    output to flag actually-changed material in CI logs; spurious lines
    erode the signal.
    """
    _make_marker(tmp_path)
    trust = _seed_trust_root(tmp_path)

    real_ca = generate_ca(trust / "ca")
    real_fp = real_ca.fingerprint

    manifest_path = trust / "manifest.yaml"
    pre = Manifest()
    pre.upsert(
        make_manifest_entry(
            name="ca",
            key_type="ca",
            fingerprint=real_fp,
            serial=1,
        )
    )
    pre.save(manifest_path)

    # Snapshot the pre-existing CA manifest entry contents so we can
    # confirm the orchestrator did not touch it.
    pre_loaded = Manifest.load(manifest_path).get("ca", "ca")
    assert pre_loaded is not None  # for the type checker

    capsys.readouterr()  # discard any noise from setup

    create_trust_material(tmp_path, hosts=["web", "db"])

    after = Manifest.load(manifest_path)
    ca_entry = after.get("ca", "ca")
    assert ca_entry is not None
    assert ca_entry.fingerprint == real_fp, "fingerprint must remain correct"
    # Same fingerprint, same serial — the entry was not re-upserted with a
    # new generated_at timestamp.
    assert ca_entry.generated_at == pre_loaded.generated_at, (
        "in-sync CA entry must NOT be re-upserted (generated_at changed)"
    )

    out = capsys.readouterr().out
    # The orchestrator may still print per-host lines on a first init run;
    # we only assert the ``ca `` line is absent.
    ca_lines = [line for line in out.splitlines() if line.startswith("ca ")]
    assert ca_lines == [], f"in-sync CA must not produce a `ca ...` line; saw: {ca_lines!r}"
