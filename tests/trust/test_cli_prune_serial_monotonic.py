# packages/ots-shared/tests/trust/test_cli_prune_serial_monotonic.py

"""Serial monotonicity across prune.

Spec ref: §113 — "per-CA monotonic ... scope-resets on CA rotation."
A pruned host's serial must NOT be reused by the next leaf signing.
The existing prune tests verify the host directory and manifest entries
are removed but do not pin the interaction with the serial counter.

Why this matters: monotonicity is the property runbooks rely on to
order issuance events. A reused serial after prune would let two leaves
share a number across the lifetime of the CA, breaking that invariant
silently.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from cryptography import x509

from ots_shared.trust import generate_keypair
from ots_shared.trust.ca import load_ca, next_serial
from ots_shared.trust.cli import app as trust_app


def _run_app(app, args: list[str]) -> int | None:
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


def test_serial_strictly_advances_after_prune(
    populated_trust_dir: Path, monkeypatch, capsys
) -> None:
    """§113: after pruning a host, the next minted leaf gets a serial strictly
    greater than the pruned host's serial.

    Setup: the populated_trust_dir fixture issued leaves for web and db.
    Read the highest serial from the on-disk leaf certs, prune web, then
    mint a fresh leaf for a new host and confirm its serial > pruned max.
    """
    trust = populated_trust_dir / ".trust"

    # Read the existing leaf serials from disk so the test does not
    # assume the fixture's exact issuance order.
    pre_serials: list[int] = []
    for role in ("web", "db"):
        cert = x509.load_pem_x509_certificate((trust / "hosts" / role / "cert.pem").read_bytes())
        pre_serials.append(cert.serial_number)
    max_pre = max(pre_serials)

    # Drop web from the marker so prune doesn't refuse on the
    # still-declared safety guard.
    _set_marker_hosts(populated_trust_dir, "db")
    monkeypatch.chdir(populated_trust_dir)

    rc = _run_app(trust_app, ["prune", "web", "--yes"])
    capsys.readouterr()
    assert rc in (None, 0), f"prune exited {rc!r}"

    # Mint a new leaf for a fresh host. Its serial must be > max_pre.
    ca = load_ca(trust / "ca")
    new_dir = trust / "hosts" / "cache"
    new_dir.mkdir(parents=True, mode=0o700)
    kp = generate_keypair("tls", "cache", new_dir, ca=ca)
    new_cert = x509.load_pem_x509_certificate((new_dir / "cert.pem").read_bytes())

    assert new_cert.serial_number > max_pre, (
        f"new leaf serial {new_cert.serial_number} did not advance past "
        f"pruned-host max {max_pre} — prune reused or rolled back the counter"
    )
    # Sanity: the keypair's exposed serial agrees with the cert.
    assert kp.serial == new_cert.serial_number


def test_serial_counter_persists_across_prune(
    populated_trust_dir: Path, monkeypatch, capsys
) -> None:
    """§113: the on-disk serial counter is not modified by prune.

    Prune deletes per-host material and manifest entries (spec §8); it must
    not touch the CA serial file because that would corrupt monotonicity
    for future issuance.
    """
    trust = populated_trust_dir / ".trust"
    serial_path = trust / "ca" / "serial"
    before = serial_path.read_bytes()
    before_mtime = serial_path.stat().st_mtime_ns

    _set_marker_hosts(populated_trust_dir, "db")
    monkeypatch.chdir(populated_trust_dir)

    rc = _run_app(trust_app, ["prune", "web", "--yes"])
    capsys.readouterr()
    assert rc in (None, 0), f"prune exited {rc!r}"

    after = serial_path.read_bytes()
    after_mtime = serial_path.stat().st_mtime_ns

    assert after == before, (
        "prune modified the CA serial counter contents; spec §113 requires "
        "monotonicity per-CA, so the counter must only ever advance via "
        "next_serial() during issuance"
    )
    assert after_mtime == before_mtime, (
        "prune touched the serial file mtime — even an idempotent rewrite "
        "is wrong here because it signals churn the operator did not request"
    )

    # Counter still advances normally afterwards.
    ca = load_ca(trust / "ca")
    n = next_serial(ca)
    assert n > 0
