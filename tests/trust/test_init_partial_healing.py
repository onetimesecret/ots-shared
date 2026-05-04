# tests/trust/test_init_partial_healing.py

"""Partial-state healing tests for create_trust_material.

Spec refs: AC #2 ("only that host's missing entries"), §6 (incremental
idempotence). The existing matrix covers whole-host add and whole-host
no-op, but it does not exercise the per-key-type presence checks
(``_ssh_files_present`` / ``_wg_files_present`` / ``_tls_files_present``)
when a single host is partially populated. A crash mid-run, a manual
deletion, or a half-applied prune produces exactly this shape; the
orchestrator must heal it without rewriting the surviving halves.
"""

from __future__ import annotations

from pathlib import Path

from ots_shared.trust.ca import generate_ca
from ots_shared.trust.init_step import create_trust_material


def _make_marker(target: Path) -> None:
    (target / "otsinfra.yaml").write_text(
        "env_name: test-partial\n"
        "created: '2026-04-25'\n"
        "hosts:\n"
        "  web:\n"
        "    private_ip_address: 10.0.0.21\n"
        "  db:\n"
        "    private_ip_address: 10.0.0.11\n"
    )


def test_partial_host_heals_missing_keytypes_only(tmp_path: Path) -> None:
    """AC #2 / §6: with a host that only has SSH on disk, init fills wg+tls
    and leaves SSH bytes byte-identical.

    Rationale: a partial state is the realistic recovery shape (a previous
    run died after writing ssh but before writing wg/tls). The orchestrator
    must heal forward, not start over. The existing
    test_add_host_materializes_only_new_entry only verifies the whole-host
    add case; this asserts the per-key-type granularity.
    """
    _make_marker(tmp_path)

    # Pre-seed: only ssh material for "web". CA must already exist for tls
    # generation on the recovery pass.
    trust = tmp_path / ".trust"
    trust.mkdir(mode=0o700)
    generate_ca(trust / "ca")
    web_dir = trust / "hosts" / "web"
    web_dir.mkdir(parents=True, mode=0o700)

    from ots_shared.trust import generate_keypair

    generate_keypair("ssh", "web", web_dir)
    ssh_priv_before = (web_dir / "ssh").read_bytes()
    ssh_pub_before = (web_dir / "ssh.pub").read_bytes()
    ssh_mtime_before = (web_dir / "ssh").stat().st_mtime_ns

    # Run the orchestrator. It must NOT touch web/ssh, and must materialize
    # web/wg* and web/{key,cert}.pem plus all of db's material.
    create_trust_material(tmp_path, hosts=["web", "db"])

    # SSH halves untouched.
    assert (web_dir / "ssh").read_bytes() == ssh_priv_before, (
        "init regenerated web/ssh despite presence on disk"
    )
    assert (web_dir / "ssh.pub").read_bytes() == ssh_pub_before
    assert (web_dir / "ssh").stat().st_mtime_ns == ssh_mtime_before, (
        "web/ssh mtime changed — file rewritten when it should not have been"
    )

    # Missing halves were generated.
    for name in ("wg", "wg.pub", "key.pem", "cert.pem"):
        assert (web_dir / name).is_file(), f"web/{name} missing — partial heal failed"

    # db got a full set.
    db_dir = trust / "hosts" / "db"
    for name in ("ssh", "ssh.pub", "wg", "wg.pub", "key.pem", "cert.pem"):
        assert (db_dir / name).is_file(), f"db/{name} missing"


def test_partial_host_heals_when_only_tls_missing(tmp_path: Path) -> None:
    """AC #2 / §6: ssh+wg present, tls missing — only tls is generated.

    This is a tighter version of the same contract: it pins the per-key-type
    presence check at the tls branch specifically, where serial allocation
    interacts with idempotence. The serial counter must advance only by
    the leaves actually issued on this run.
    """
    _make_marker(tmp_path)

    trust = tmp_path / ".trust"
    trust.mkdir(mode=0o700)
    ca = generate_ca(trust / "ca")
    web_dir = trust / "hosts" / "web"
    web_dir.mkdir(parents=True, mode=0o700)

    from ots_shared.trust import generate_keypair
    from ots_shared.trust.ca import next_serial, reset_serial

    generate_keypair("ssh", "web", web_dir)
    generate_keypair("wg", "web", web_dir)

    ssh_bytes_before = (web_dir / "ssh").read_bytes()
    wg_bytes_before = (web_dir / "wg").read_bytes()

    # Reset the serial so we can prove only one leaf was issued for web
    # (db's leaf will burn the next number).
    reset_serial(ca)

    create_trust_material(tmp_path, hosts=["web", "db"])

    # web's ssh and wg untouched.
    assert (web_dir / "ssh").read_bytes() == ssh_bytes_before
    assert (web_dir / "wg").read_bytes() == wg_bytes_before
    # web's tls now exists.
    assert (web_dir / "key.pem").is_file()
    assert (web_dir / "cert.pem").is_file()

    # Spec §113: TLS *and* WG both bump the per-CA monotonic counter (WG
    # inherits the value for timeline accounting). On this run, three
    # increments happen: web tls, db wg, db tls. SSH does not pass ca=, so
    # no bump for either ssh entry. Counter sits at 3 after the run, so
    # the next allocation returns 4.
    assert next_serial(ca) == 4, (
        "expected three serial bumps (web tls + db wg + db tls); serial counter disagrees"
    )
