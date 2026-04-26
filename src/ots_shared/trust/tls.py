"""TLS leaves signed by the local CA.

SAN convention: ``DNS:<role>`` and ``DNS:<role>.local``. Subject CN = ``<role>``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.x509.oid import NameOID

from ._paths import write_private, write_public
from .ca import CA, load_ca_certificate, load_ca_private_key
from .fingerprint import compute_fingerprint


def _spki_bytes(public_key: ed25519.Ed25519PublicKey) -> bytes:
    return public_key.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def _build_leaf(
    name: str,
    leaf_key: ed25519.Ed25519PrivateKey,
    ca_cert: x509.Certificate,
    ca_key: ed25519.Ed25519PrivateKey,
    serial: int,
    days: int,
) -> x509.Certificate:
    now = datetime.now(UTC)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    san = x509.SubjectAlternativeName(
        [x509.DNSName(name), x509.DNSName(f"{name}.local")]
    )
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(leaf_key.public_key())
        .serial_number(serial)
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=days))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage(
                [x509.oid.ExtendedKeyUsageOID.SERVER_AUTH, x509.oid.ExtendedKeyUsageOID.CLIENT_AUTH]
            ),
            critical=False,
        )
        .add_extension(san, critical=False)
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(leaf_key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_cert.public_key()),  # type: ignore[arg-type]
            critical=False,
        )
    )
    return builder.sign(private_key=ca_key, algorithm=None)


def generate_tls_leaf(
    name: str,
    host_dir: Path,
    ca: CA,
    serial: int,
    days: int,
) -> tuple[Path, Path, str]:
    key_path = host_dir / "key.pem"
    cert_path = host_dir / "cert.pem"

    if key_path.exists() and cert_path.exists():
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        public_key = cert.public_key()
        if not isinstance(public_key, ed25519.Ed25519PublicKey):
            raise ValueError(f"TLS leaf for {name} is not ed25519")
        fingerprint = compute_fingerprint(_spki_bytes(public_key))
        return key_path, cert_path, fingerprint

    leaf_key = ed25519.Ed25519PrivateKey.generate()
    ca_cert = load_ca_certificate(ca)
    ca_key = load_ca_private_key(ca)
    cert = _build_leaf(name, leaf_key, ca_cert, ca_key, serial, days)

    key_pem = leaf_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)

    write_private(key_path, key_pem)
    write_public(cert_path, cert_pem)

    fingerprint = compute_fingerprint(_spki_bytes(leaf_key.public_key()))
    return key_path, cert_path, fingerprint
