"""WireGuard Curve25519 keypair, base64-encoded per WG conventions."""

from __future__ import annotations

import base64
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import x25519

from ._paths import write_private, write_public
from .fingerprint import compute_fingerprint


def _raw_private(private_key: x25519.X25519PrivateKey) -> bytes:
    return private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _raw_public(public_key: x25519.X25519PublicKey) -> bytes:
    return public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def generate_wg_keypair(name: str, host_dir: Path) -> tuple[Path, Path, str]:
    del name  # WG files carry no role comment; metadata lives in the manifest.
    private_path = host_dir / "wg"
    public_path = host_dir / "wg.pub"

    if private_path.exists() and public_path.exists():
        raw_pub = base64.b64decode(public_path.read_text().strip())
        return private_path, public_path, compute_fingerprint(raw_pub)

    private_key = x25519.X25519PrivateKey.generate()
    public_key = private_key.public_key()

    private_b64 = base64.b64encode(_raw_private(private_key)) + b"\n"
    public_raw = _raw_public(public_key)
    public_b64 = base64.b64encode(public_raw) + b"\n"

    write_private(private_path, private_b64)
    write_public(public_path, public_b64)

    return private_path, public_path, compute_fingerprint(public_raw)
