"""Fingerprint helpers shared across trust primitives.

SSH: matches `ssh-keygen -E sha256 -lf` (SHA256 over the OpenSSH public-key blob).
WG:  SHA256 over the raw 32-byte Curve25519 public key.
TLS: SHA256 over the X.509 SubjectPublicKeyInfo DER.
All formatted as ``SHA256:<base64-no-pad>`` (standard base64, padding stripped).

The public entrypoint accepts either raw public bytes (already-decoded) or a
``Path`` to one of the on-disk public files (`ssh.pub`, `wg.pub`, `cert.pem`).
File-shape dispatch is by content, not extension.
"""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import serialization

_PREFIX = "SHA256:"


def _digest(data: bytes) -> str:
    encoded = base64.b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode("ascii")
    return f"{_PREFIX}{encoded}"


def _ssh_blob_from_pub_line(line: bytes) -> bytes:
    """Decode the base64 blob from an OpenSSH public-key line.

    Format: ``<algo> <base64-blob> [comment]``. ssh-keygen -lf hashes the
    decoded blob, not the textual line, so we mirror that.
    """
    parts = line.split()
    if len(parts) < 2:
        raise ValueError("Malformed SSH public key file (no base64 blob)")
    return base64.b64decode(parts[1])


def _spki_from_cert_pem(pem: bytes) -> bytes:
    cert = x509.load_pem_x509_certificate(pem)
    return cert.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def compute_fingerprint(source: Path | bytes) -> str:
    """Return ``SHA256:<base64>`` for the supplied public material.

    If ``source`` is bytes, hash them directly (caller has already extracted
    the canonical public-bytes representation). If ``source`` is a Path,
    auto-detect the file shape: PEM certificate → SPKI; OpenSSH public-key
    line → blob; otherwise treat the file as a base64-encoded raw key (WG).
    """
    if isinstance(source, (bytes, bytearray)):
        return _digest(bytes(source))

    raw = source.read_bytes()
    text = raw.lstrip()
    if text.startswith(b"-----BEGIN CERTIFICATE-----"):
        return _digest(_spki_from_cert_pem(raw))
    if text.startswith(b"ssh-") or text.startswith(b"ecdsa-") or text.startswith(b"sk-"):
        return _digest(_ssh_blob_from_pub_line(text))
    # WG public file: single line of base64 over 32 raw bytes.
    decoded = base64.b64decode(text.strip())
    return _digest(decoded)
