"""generate_keypair tests across the key_type axis (ssh, wg, tls).

Spec refs: §3 (single primitive), §6 (idempotence), §73-76 (modes),
§112 (validity), §113 (serials).
"""

from __future__ import annotations

import base64
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.ec import ECDSA, EllipticCurvePublicKey
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey

from ots_shared.trust import generate_keypair
from ots_shared.trust.ca import CA, next_serial


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


# ---- key-type contract ------------------------------------------------------


def test_ssh_ignores_ca_param(trust_dir: Path, ca: CA) -> None:
    """§3: passing ca= to ssh keytype produces the same artifacts as omitting it."""
    out_a = trust_dir / "hosts" / "ssh-with-ca"
    out_b = trust_dir / "hosts" / "ssh-without-ca"

    kp_a = generate_keypair("ssh", "host-a", out_a, ca=ca)
    kp_b = generate_keypair("ssh", "host-b", out_b)

    # Both produce the expected artifacts.
    assert (out_a / "ssh").exists() and (out_a / "ssh.pub").exists()
    assert (out_b / "ssh").exists() and (out_b / "ssh.pub").exists()
    # Both keypairs were generated independently, so fingerprints differ —
    # what matters is that the call shape worked at all (no error, no extra
    # files written for the ca-passed branch).
    assert kp_a.fingerprint != kp_b.fingerprint
    # No CA-derived files in the ssh out-dir.
    for stray in ("cert.pem", "key.pem"):
        assert not (out_a / stray).exists(), f"ssh + ca must not write {stray}"


def test_wg_ignores_ca_param(trust_dir: Path, ca: CA) -> None:
    """§3: passing ca= to wg keytype produces the same artifacts as omitting it."""
    out_a = trust_dir / "hosts" / "wg-with-ca"
    out_b = trust_dir / "hosts" / "wg-without-ca"

    generate_keypair("wg", "host-a", out_a, ca=ca)
    generate_keypair("wg", "host-b", out_b)

    assert (out_a / "wg").exists() and (out_a / "wg.pub").exists()
    assert (out_b / "wg").exists() and (out_b / "wg.pub").exists()
    for stray in ("cert.pem", "key.pem"):
        assert not (out_a / stray).exists(), f"wg + ca must not write {stray}"


def test_tls_requires_ca(trust_dir: Path) -> None:
    """§3: tls keytype without ca= must raise ValueError."""
    out = trust_dir / "hosts" / "tls-no-ca"
    with pytest.raises(ValueError):
        generate_keypair("tls", "host-a", out)


def test_tls_uses_ca_serial(trust_dir: Path, ca: CA) -> None:
    """§113: the tls leaf serial == the value next_serial(ca) returns at gen time.

    We set the counter to a known value, generate the leaf, and assert the
    leaf cert serial matches what the next call would have produced.
    """
    from ots_shared.trust.ca import reset_serial

    reset_serial(ca)
    out = trust_dir / "hosts" / "tls-host"
    generate_keypair("tls", "host-a", out, ca=ca)

    cert = x509.load_pem_x509_certificate((out / "cert.pem").read_bytes())
    # First leaf after reset uses serial 1.
    assert cert.serial_number == 1, (
        f"first tls leaf after reset must use serial 1, got {cert.serial_number}"
    )

    # Subsequent leaf bumps the counter.
    out2 = trust_dir / "hosts" / "tls-host-2"
    generate_keypair("tls", "host-b", out2, ca=ca)
    cert2 = x509.load_pem_x509_certificate((out2 / "cert.pem").read_bytes())
    assert cert2.serial_number == 2


def test_default_leaf_validity_is_24_months(trust_dir: Path, ca: CA) -> None:
    """§112: tls leaf default validity is 730 days (24 months), ±1 day."""
    out = trust_dir / "hosts" / "tls-validity"
    generate_keypair("tls", "host-a", out, ca=ca)

    cert = x509.load_pem_x509_certificate((out / "cert.pem").read_bytes())
    not_after = cert.not_valid_after_utc
    expected = datetime.now(tz=UTC) + timedelta(days=730)
    delta = abs((not_after - expected).total_seconds())
    assert delta < 86400, f"leaf not_valid_after off by {delta}s; expected ~730d from now"


# ---- idempotence ------------------------------------------------------------


def test_idempotent_returns_existing_ssh(trust_dir: Path) -> None:
    """§6: second generate_keypair on the same out_dir returns the existing fingerprint."""
    out = trust_dir / "hosts" / "ssh-idem"
    kp1 = generate_keypair("ssh", "host-a", out)
    priv_bytes = (out / "ssh").read_bytes()
    pub_bytes = (out / "ssh.pub").read_bytes()
    mtime = (out / "ssh").stat().st_mtime_ns

    kp2 = generate_keypair("ssh", "host-a", out)

    assert kp1.fingerprint == kp2.fingerprint
    assert (out / "ssh").read_bytes() == priv_bytes
    assert (out / "ssh.pub").read_bytes() == pub_bytes
    assert (out / "ssh").stat().st_mtime_ns == mtime, "private must not be rewritten"


def test_idempotent_returns_existing_wg(trust_dir: Path) -> None:
    """§6: same as above for wg."""
    out = trust_dir / "hosts" / "wg-idem"
    kp1 = generate_keypair("wg", "host-a", out)
    priv_bytes = (out / "wg").read_bytes()
    mtime = (out / "wg").stat().st_mtime_ns

    kp2 = generate_keypair("wg", "host-a", out)

    assert kp1.fingerprint == kp2.fingerprint
    assert (out / "wg").read_bytes() == priv_bytes
    assert (out / "wg").stat().st_mtime_ns == mtime


def test_idempotent_returns_existing_tls(trust_dir: Path, ca: CA) -> None:
    """§6: idempotence for tls. Critical: re-call must not consume a serial."""
    out = trust_dir / "hosts" / "tls-idem"
    kp1 = generate_keypair("tls", "host-a", out, ca=ca)
    cert_bytes = (out / "cert.pem").read_bytes()
    key_bytes = (out / "key.pem").read_bytes()

    serial_before_second_call = next_serial(ca)  # peek and bump
    # Re-generate the same host: must not write or sign anything new.
    kp2 = generate_keypair("tls", "host-a", out, ca=ca)

    assert kp1.fingerprint == kp2.fingerprint
    assert (out / "cert.pem").read_bytes() == cert_bytes
    assert (out / "key.pem").read_bytes() == key_bytes
    # Serial counter must not have been touched by the no-op generate.
    assert next_serial(ca) == serial_before_second_call + 1


# ---- modes ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key_type", "private_name", "public_name"),
    [
        ("ssh", "ssh", "ssh.pub"),
        ("wg", "wg", "wg.pub"),
    ],
)
def test_modes_after_generation_bare(
    trust_dir: Path, key_type: str, private_name: str, public_name: str
) -> None:
    """§73-76: private halves 0600, public halves 0644 for ssh and wg."""
    out = trust_dir / "hosts" / f"modes-{key_type}"
    generate_keypair(key_type, "host-a", out)  # type: ignore[arg-type]

    assert _mode(out / private_name) == 0o600, f"{private_name} must be 0600"
    assert _mode(out / public_name) == 0o644, f"{public_name} must be 0644"


def test_modes_after_generation_tls(trust_dir: Path, ca: CA) -> None:
    """§73-76: tls leaf key 0600, leaf cert 0644."""
    out = trust_dir / "hosts" / "modes-tls"
    generate_keypair("tls", "host-a", out, ca=ca)

    assert _mode(out / "key.pem") == 0o600, "tls key.pem must be 0600"
    assert _mode(out / "cert.pem") == 0o644, "tls cert.pem must be 0644"


# ---- algorithm conventions --------------------------------------------------


def test_ssh_keypair_is_ed25519(trust_dir: Path) -> None:
    """ssh public key serializes as 'ssh-ed25519 ...'."""
    out = trust_dir / "hosts" / "ssh-algo"
    generate_keypair("ssh", "host-a", out)

    pub = (out / "ssh.pub").read_text().strip()
    assert pub.startswith("ssh-ed25519 "), f"expected ssh-ed25519 line, got: {pub[:40]}..."


def test_wg_keypair_is_curve25519(trust_dir: Path) -> None:
    """wg private and public are 32-byte raw, base64-encoded (44-char b64 with '=')."""
    out = trust_dir / "hosts" / "wg-algo"
    generate_keypair("wg", "host-a", out)

    priv_b64 = (out / "wg").read_text().strip()
    pub_b64 = (out / "wg.pub").read_text().strip()

    priv_raw = base64.b64decode(priv_b64)
    pub_raw = base64.b64decode(pub_b64)

    assert len(priv_raw) == 32, f"wg private must be 32 raw bytes, got {len(priv_raw)}"
    assert len(pub_raw) == 32, f"wg public must be 32 raw bytes, got {len(pub_raw)}"


def test_tls_leaf_signed_by_ca(trust_dir: Path, ca: CA) -> None:
    """§2: leaf cert verifies under the CA's public key."""
    out = trust_dir / "hosts" / "tls-signed"
    generate_keypair("tls", "host-a", out, ca=ca)

    leaf = x509.load_pem_x509_certificate((out / "cert.pem").read_bytes())

    # Pull the CA cert from its on-disk location to keep the test independent
    # of CA object internals.
    ca_dir = trust_dir / "ca"
    ca_cert = x509.load_pem_x509_certificate((ca_dir / "ca.crt").read_bytes())
    ca_pub = ca_cert.public_key()

    sig_alg_oid = leaf.signature_algorithm_oid
    sig_hash = leaf.signature_hash_algorithm

    if isinstance(ca_pub, RSAPublicKey):
        assert sig_hash is not None
        ca_pub.verify(
            leaf.signature,
            leaf.tbs_certificate_bytes,
            padding.PKCS1v15(),
            sig_hash,
        )
    elif isinstance(ca_pub, EllipticCurvePublicKey):
        assert sig_hash is not None
        ca_pub.verify(leaf.signature, leaf.tbs_certificate_bytes, ECDSA(sig_hash))
    elif isinstance(ca_pub, Ed25519PublicKey):
        ca_pub.verify(leaf.signature, leaf.tbs_certificate_bytes)
    else:  # pragma: no cover - defensive
        pytest.fail(f"unsupported CA key type: {type(ca_pub).__name__} (sig oid {sig_alg_oid})")

    # Issuer of leaf == subject of CA.
    assert leaf.issuer == ca_cert.subject
    # Sanity that the hash machinery is wired (suppress unused-var lint).
    _ = hashes.SHA256
