"""CA generation, loading, and serial counter tests.

Spec refs: §73-76 (modes), §111 (path), §112 (validity), §113 (serial), §6 (idempotence).
"""

from __future__ import annotations

import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization

from ots_shared.trust.ca import CA, generate_ca, load_ca, next_serial, reset_serial


def _mode(path: Path) -> int:
    """Return the file's permission bits (low 12 bits)."""
    return stat.S_IMODE(path.stat().st_mode)


def test_generate_creates_cert_key_serial_with_modes(trust_dir: Path) -> None:
    """§73-76, §111: CA emits cert/key/serial files at expected paths and modes,
    and the cert is a CA (basicConstraints CA:TRUE)."""
    ca_dir = trust_dir / "ca"
    ca_obj = generate_ca(ca_dir)

    cert_path = ca_dir / "ca.crt"
    key_path = ca_dir / "ca.key"
    serial_path = ca_dir / "serial"

    assert cert_path.exists(), "CA cert must exist"
    assert key_path.exists(), "CA private key must exist"
    assert serial_path.exists(), "CA serial counter must exist"

    assert _mode(cert_path) == 0o644
    assert _mode(key_path) == 0o600
    assert _mode(serial_path) == 0o644

    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    bc = cert.extensions.get_extension_for_class(x509.BasicConstraints)
    assert bc.value.ca is True, "CA cert must have basicConstraints CA:TRUE"

    # The CA object exposes a path back to its directory.
    assert isinstance(ca_obj, CA)


def test_generate_idempotent_returns_existing(trust_dir: Path) -> None:
    """§6: a second call must NOT rewrite existing material."""
    ca_dir = trust_dir / "ca"
    first = generate_ca(ca_dir)
    cert_path = ca_dir / "ca.crt"
    key_path = ca_dir / "ca.key"

    first_cert_bytes = cert_path.read_bytes()
    first_key_bytes = key_path.read_bytes()
    first_cert_mtime = cert_path.stat().st_mtime_ns

    second = generate_ca(ca_dir)

    assert cert_path.read_bytes() == first_cert_bytes, "CA cert bytes must be unchanged"
    assert key_path.read_bytes() == first_key_bytes, "CA key bytes must be unchanged"
    assert cert_path.stat().st_mtime_ns == first_cert_mtime, "CA cert must not be rewritten"

    # Same fingerprint => same identity.
    assert first.fingerprint == second.fingerprint


def test_load_missing_raises(trust_dir: Path) -> None:
    """load_ca on a directory with no CA must raise FileNotFoundError."""
    ca_dir = trust_dir / "ca"
    with pytest.raises(FileNotFoundError):
        load_ca(ca_dir)


def test_next_serial_monotonic(trust_dir: Path) -> None:
    """§113: sequential calls return strictly monotonic 1, 2, 3."""
    ca_obj = generate_ca(trust_dir / "ca")
    reset_serial(ca_obj)

    assert next_serial(ca_obj) == 1
    assert next_serial(ca_obj) == 2
    assert next_serial(ca_obj) == 3


def test_reset_serial_zeroes(trust_dir: Path) -> None:
    """§113: reset_serial puts the counter back to 0."""
    ca_obj = generate_ca(trust_dir / "ca")
    next_serial(ca_obj)
    next_serial(ca_obj)
    reset_serial(ca_obj)

    # First call after reset returns 1 (i.e., counter was at 0 pre-increment).
    assert next_serial(ca_obj) == 1


def test_default_validity_is_four_years(trust_dir: Path) -> None:
    """§112: CA default validity is 4 years (1460 days), ±1 day tolerance."""
    ca_dir = trust_dir / "ca"
    generate_ca(ca_dir)

    cert = x509.load_pem_x509_certificate((ca_dir / "ca.crt").read_bytes())
    not_after = cert.not_valid_after_utc
    expected = datetime.now(tz=UTC) + timedelta(days=1460)

    delta = abs((not_after - expected).total_seconds())
    assert delta < 86400, f"CA not_valid_after off by {delta}s; expected ~1460d from now"


def test_ca_key_is_loadable(trust_dir: Path) -> None:
    """Sanity: the CA private key file is a valid PEM private key."""
    ca_dir = trust_dir / "ca"
    generate_ca(ca_dir)

    key = serialization.load_pem_private_key(
        (ca_dir / "ca.key").read_bytes(),
        password=None,
    )
    # Either RSA or EC/Ed25519 — we only check it loads.
    assert key is not None
