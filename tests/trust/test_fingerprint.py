"""Fingerprint computation tests.

Spec refs: §14 (deterministic SHA256: fingerprints), §65 (no randomart).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from ots_shared.trust import generate_keypair
from ots_shared.trust.fingerprint import compute_fingerprint


def test_deterministic(trust_dir: Path) -> None:
    """§14: same input bytes must yield the same fingerprint string."""
    out = trust_dir / "hosts" / "fp-det"
    generate_keypair("ssh", "host-a", out)
    pub = out / "ssh.pub"

    fp1 = compute_fingerprint(pub)
    fp2 = compute_fingerprint(pub)
    assert fp1 == fp2


def test_format_is_sha256_prefix(trust_dir: Path) -> None:
    """§14: fingerprints are SHA256-prefixed strings."""
    out = trust_dir / "hosts" / "fp-fmt"
    generate_keypair("ssh", "host-a", out)
    fp = compute_fingerprint(out / "ssh.pub")

    assert fp.startswith("SHA256:"), f"expected SHA256: prefix, got {fp!r}"


def test_no_randomart(trust_dir: Path) -> None:
    """§65: compute_fingerprint returns a single line — no multi-line ascii art."""
    out = trust_dir / "hosts" / "fp-noart"
    generate_keypair("ssh", "host-a", out)
    fp = compute_fingerprint(out / "ssh.pub")

    assert "\n" not in fp.rstrip("\n"), f"fingerprint must be single-line, got {fp!r}"
    assert "+" not in fp or "----" not in fp, "fingerprint must not contain randomart frame"


def test_ssh_matches_ssh_keygen(trust_dir: Path) -> None:
    """§14: for an ed25519 pubkey, compute_fingerprint matches `ssh-keygen -E sha256 -lf`."""
    if shutil.which("ssh-keygen") is None:
        pytest.skip("ssh-keygen not on PATH")

    out = trust_dir / "hosts" / "fp-skg"
    generate_keypair("ssh", "host-a", out)
    pub_path = out / "ssh.pub"

    result = subprocess.run(
        ["ssh-keygen", "-E", "sha256", "-lf", str(pub_path)],
        capture_output=True,
        text=True,
        check=True,
    )
    # Output: "<bits> SHA256:<base64> comment (TYPE)"
    parts = result.stdout.strip().split()
    skg_fp = next((p for p in parts if p.startswith("SHA256:")), None)
    assert skg_fp is not None, f"ssh-keygen output had no SHA256 token: {result.stdout!r}"

    ours = compute_fingerprint(pub_path)
    assert ours == skg_fp, f"compute_fingerprint={ours!r} vs ssh-keygen={skg_fp!r}"
