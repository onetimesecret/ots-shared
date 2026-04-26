"""Cross-cutting file mode assertions.

Spec ref: §73-76 (directory modes 0700, private 0600, public 0644).

The per-file mode tests for ca/keypair/manifest live in their own modules.
This module focuses on the directory-mode invariant which spans all of them.
"""

from __future__ import annotations

import stat
from pathlib import Path

from ots_shared.trust import generate_keypair
from ots_shared.trust.ca import CA, generate_ca


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_directory_modes_0700_for_ca(trust_dir: Path) -> None:
    """§73-76: the CA parent dir is created with mode 0700."""
    ca_dir = trust_dir / "ca"
    generate_ca(ca_dir)

    # Both the .trust/ root and the ca/ subdir must be 0700.
    assert trust_dir.exists()
    assert _mode(trust_dir) == 0o700, f".trust/ must be 0700, got {_mode(trust_dir):o}"
    assert _mode(ca_dir) == 0o700, f".trust/ca must be 0700, got {_mode(ca_dir):o}"


def test_directory_modes_0700_for_hosts(trust_dir: Path, ca: CA) -> None:
    """§73-76: per-host parent dirs under .trust/hosts/ are created with mode 0700."""
    out = trust_dir / "hosts" / "host-a"
    generate_keypair("ssh", "host-a", out)

    assert _mode(trust_dir / "hosts") == 0o700, "hosts/ parent must be 0700"
    assert _mode(out) == 0o700, "per-host dir must be 0700"


def test_directory_modes_0700_for_tls_host(trust_dir: Path, ca: CA) -> None:
    """§73-76: tls path also gets 0700 on the per-host dir."""
    out = trust_dir / "hosts" / "tls-host"
    generate_keypair("tls", "host-a", out, ca=ca)

    assert _mode(out) == 0o700, "tls per-host dir must be 0700"
