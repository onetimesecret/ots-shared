"""SSH host keys: ed25519 keypair in OpenSSH format."""

from __future__ import annotations

from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from ._paths import write_private, write_public
from .fingerprint import compute_fingerprint


def _openssh_public_blob(public_key: ed25519.Ed25519PublicKey) -> bytes:
    """OpenSSH wire format of the public key (used for ssh-keygen-style fingerprint)."""
    openssh_line = public_key.public_bytes(
        encoding=serialization.Encoding.OpenSSH,
        format=serialization.PublicFormat.OpenSSH,
    )
    # Format: "ssh-ed25519 <base64-blob> [comment]" — extract the base64 blob.
    parts = openssh_line.split()
    if len(parts) < 2:
        raise ValueError("Unexpected OpenSSH public key format")
    import base64

    return base64.b64decode(parts[1])


def generate_ssh_keypair(name: str, host_dir: Path) -> tuple[Path, Path, str]:
    private_path = host_dir / "ssh"
    public_path = host_dir / "ssh.pub"

    if private_path.exists() and public_path.exists():
        public_line = public_path.read_bytes()
        public_key = serialization.load_ssh_public_key(public_line)
        if not isinstance(public_key, ed25519.Ed25519PublicKey):
            raise ValueError(f"SSH public key for {name} is not ed25519")
        fingerprint = compute_fingerprint(_openssh_public_blob(public_key))
        return private_path, public_path, fingerprint

    private_key = ed25519.Ed25519PrivateKey.generate()
    public_key = private_key.public_key()

    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.OpenSSH,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_line = public_key.public_bytes(
        encoding=serialization.Encoding.OpenSSH,
        format=serialization.PublicFormat.OpenSSH,
    )
    public_with_comment = public_line + b" " + name.encode("utf-8") + b"\n"

    write_private(private_path, private_pem)
    write_public(public_path, public_with_comment)

    fingerprint = compute_fingerprint(_openssh_public_blob(public_key))
    return private_path, public_path, fingerprint
