"""Local CA: self-signed root that signs per-host TLS leaves."""

from __future__ import annotations

import fcntl
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.x509.oid import NameOID

from ._paths import ensure_dir, write_private, write_public
from .fingerprint import compute_fingerprint

_CA_CN = "otsinfra-ca"


@dataclass(frozen=True)
class CA:
    cert_path: Path
    key_path: Path
    serial_path: Path
    fingerprint: str


def _paths(out_dir: Path) -> tuple[Path, Path, Path]:
    return out_dir / "ca.crt", out_dir / "ca.key", out_dir / "serial"


def _spki_bytes(public_key: ed25519.Ed25519PublicKey) -> bytes:
    return public_key.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def _build_self_signed(
    private_key: ed25519.Ed25519PrivateKey, days: int
) -> x509.Certificate:
    now = datetime.now(UTC)
    subject = issuer = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, _CA_CN)]
    )
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(1)
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=days))
        .add_extension(
            x509.BasicConstraints(ca=True, path_length=0), critical=True
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(private_key.public_key()),
            critical=False,
        )
    )
    return builder.sign(private_key=private_key, algorithm=None)


def generate_ca(out_dir: Path, days: int = 1460) -> CA:
    cert_path, key_path, serial_path = _paths(out_dir)
    if cert_path.exists() and key_path.exists() and serial_path.exists():
        return load_ca(out_dir)

    ensure_dir(out_dir)

    private_key = ed25519.Ed25519PrivateKey.generate()
    cert = _build_self_signed(private_key, days)

    key_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)

    write_private(key_path, key_pem)
    write_public(cert_path, cert_pem)
    write_public(serial_path, b"0\n")

    fingerprint = compute_fingerprint(_spki_bytes(private_key.public_key()))
    return CA(
        cert_path=cert_path,
        key_path=key_path,
        serial_path=serial_path,
        fingerprint=fingerprint,
    )


def load_ca(out_dir: Path) -> CA:
    cert_path, key_path, serial_path = _paths(out_dir)
    if not (cert_path.exists() and key_path.exists() and serial_path.exists()):
        raise FileNotFoundError(f"CA material missing under {out_dir}")
    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    public_key = cert.public_key()
    if not isinstance(public_key, ed25519.Ed25519PublicKey):
        raise ValueError("CA certificate does not carry an ed25519 public key")
    fingerprint = compute_fingerprint(_spki_bytes(public_key))
    return CA(
        cert_path=cert_path,
        key_path=key_path,
        serial_path=serial_path,
        fingerprint=fingerprint,
    )


def _read_serial_locked(serial_path: Path) -> int:
    text = serial_path.read_text().strip()
    return int(text) if text else 0


def next_serial(ca: CA) -> int:
    """Atomically increment and return the per-CA monotonic serial."""
    with open(ca.serial_path, "r+") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            current = _read_serial_locked(ca.serial_path)
            updated = current + 1
            fh.seek(0)
            fh.truncate()
            fh.write(f"{updated}\n")
            fh.flush()
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    ca.serial_path.chmod(0o644)
    return updated


def reset_serial(ca: CA) -> None:
    with open(ca.serial_path, "r+") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            fh.seek(0)
            fh.truncate()
            fh.write("0\n")
            fh.flush()
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    ca.serial_path.chmod(0o644)


def load_ca_private_key(ca: CA) -> ed25519.Ed25519PrivateKey:
    key = serialization.load_pem_private_key(ca.key_path.read_bytes(), password=None)
    if not isinstance(key, ed25519.Ed25519PrivateKey):
        raise ValueError("CA private key is not ed25519")
    return key


def load_ca_certificate(ca: CA) -> x509.Certificate:
    return x509.load_pem_x509_certificate(ca.cert_path.read_bytes())


# Re-export for module callers that prefer x509-typed handles
_ = hashes  # keep import surface stable for future SHA-based extension hooks
