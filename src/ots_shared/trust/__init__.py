"""Trust material primitives for the .trust/ operator-checkout layout.

Public surface: ``generate_keypair`` is the single primitive across SSH host keys,
WireGuard Curve25519 keypairs, and TLS x509 leaves; ``key_type`` is the discriminant.
The CA, manifest, and fingerprint helpers are exported from their modules.
"""

from __future__ import annotations

import contextlib
import fcntl
import getpass
import os
import socket
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from ._paths import ensure_dir
from .ca import CA, generate_ca, load_ca, next_serial, reset_serial
from .fingerprint import compute_fingerprint
from .manifest import Manifest, ManifestEntry
from .ssh import generate_ssh_keypair
from .tls import generate_tls_leaf
from .wg import generate_wg_keypair

TRUST_DIRNAME = ".trust"


class OtsInfraMarkerMissingError(RuntimeError):
    """Raised when ``.otsinfra.yaml`` cannot be located via walk-up.

    The marker anchors the operator checkout root; trust paths derive
    from it. Callers translate this into their own user-facing error
    (e.g. cli exit, render failure) with their own actionable message.
    """


def resolve_trust_dir(start: Path | None = None) -> Path:
    """Locate ``.trust/`` anchored off the ``.otsinfra.yaml`` marker.

    Walk-up discovery uses :func:`ots_shared.ssh.env.find_marker`. The
    returned path is ``<marker_parent>/.trust/`` and may not exist on
    disk — callers that need on-disk material check that themselves.

    Raises :class:`OtsInfraMarkerMissingError` when no marker is found.
    """
    # Imported here to avoid a top-level cycle (ssh.env imports nothing
    # from this module today, but keeping it lazy is cheap insurance).
    from ots_shared.ssh.env import find_marker

    marker = find_marker(start)
    if marker is None:
        raise OtsInfraMarkerMissingError(
            "no .otsinfra.yaml marker found via walk-up; "
            "run from inside an OTS environment checkout"
        )
    return marker.parent / TRUST_DIRNAME


@contextlib.contextmanager
def trust_flock(target: Path) -> Iterator[None]:
    """Hold an exclusive blocking flock for the ``.trust/`` generation path.

    Spec §77: a flock guards generation so two simultaneous invocations from
    the same checkout converge on a single trust state (AC #5). The lock
    target is the operator-checkout directory ``target`` itself — it is
    stable, pre-existing, and lives *outside* ``.trust/``. Locking on
    ``.trust/`` directly would break under ``--force``: ``rmtree(.trust)``
    would unlink the lock target's inode while a concurrent caller holds
    the original fd, after which a second ``open(.trust)`` would resolve
    to a fresh inode and the two processes would proceed in parallel.
    """
    fd = os.open(target, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)

KeyType = Literal["ssh", "wg", "tls"]


@dataclass(frozen=True)
class Keypair:
    name: str
    key_type: KeyType
    private_path: Path
    public_path: Path
    cert_path: Path | None
    fingerprint: str
    serial: int | None


def _resolve_user(user: str | None) -> str:
    if user:
        return user
    return os.environ.get("USER") or getpass.getuser()


def _resolve_hostname(hostname: str | None) -> str:
    if hostname:
        return hostname
    return socket.gethostname()


def generate_keypair(
    key_type: KeyType,
    name: str,
    out_dir: Path,
    *,
    ca: CA | None = None,
    leaf_days: int = 730,
    user: str | None = None,
    hostname: str | None = None,
) -> Keypair:
    if key_type == "tls" and ca is None:
        raise ValueError("ca is required for key_type='tls'")

    ensure_dir(out_dir)

    serial: int | None = None
    if key_type == "ssh":
        private_path, public_path, fingerprint = generate_ssh_keypair(name, out_dir)
        cert_path: Path | None = None
        # SSH does not consume a CA serial (no x509 issuance, no §113 timeline
        # binding). The orchestrator never passes ca= for ssh; we honor that
        # by ignoring the parameter even when supplied.
    elif key_type == "wg":
        # Spec §113: WG inherits the per-CA serial for timeline accounting.
        # Bump only when minting fresh material; idempotent re-load must not
        # consume a serial. Pre-flight existence check before the keypair
        # call decides whether this run is a fresh mint or a no-op load.
        wg_priv_path = out_dir / "wg"
        wg_pub_path = out_dir / "wg.pub"
        wg_freshly_minted = not (wg_priv_path.exists() and wg_pub_path.exists())
        private_path, public_path, fingerprint = generate_wg_keypair(name, out_dir)
        cert_path = None
        if ca is not None and wg_freshly_minted:
            serial = next_serial(ca)
    elif key_type == "tls":
        assert ca is not None  # for the type checker; validated above
        # Allocate serial only when minting a fresh leaf; idempotent re-load reuses
        # the existing on-disk material and leaves the serial counter untouched.
        leaf_key_path = out_dir / "key.pem"
        leaf_cert_path = out_dir / "cert.pem"
        if leaf_key_path.exists() and leaf_cert_path.exists():
            issued_serial = 0
        else:
            issued_serial = next_serial(ca)
        private_path, cert_path, fingerprint = generate_tls_leaf(
            name, out_dir, ca, issued_serial, leaf_days
        )
        public_path = cert_path
        serial = issued_serial if issued_serial else None
    else:
        raise ValueError(f"Unknown key_type: {key_type}")

    # Caller-side metadata — manifest writes are the orchestrator's responsibility,
    # but we surface user/hostname here so the orchestrator can stamp entries
    # without re-resolving in two places.
    _ = (_resolve_user(user), _resolve_hostname(hostname))

    return Keypair(
        name=name,
        key_type=key_type,
        private_path=private_path,
        public_path=public_path,
        cert_path=cert_path,
        fingerprint=fingerprint,
        serial=serial,
    )


__all__ = [
    "CA",
    "KeyType",
    "Keypair",
    "Manifest",
    "ManifestEntry",
    "OtsInfraMarkerMissingError",
    "TRUST_DIRNAME",
    "compute_fingerprint",
    "generate_ca",
    "generate_keypair",
    "load_ca",
    "next_serial",
    "reset_serial",
    "resolve_trust_dir",
    "trust_flock",
]


# Builders that need the resolved identity (used by orchestrators) ----------

def make_manifest_entry(
    *,
    name: str,
    key_type: str,
    fingerprint: str,
    serial: int,
    user: str | None = None,
    hostname: str | None = None,
    generated_at: datetime | None = None,
) -> ManifestEntry:
    return ManifestEntry(
        name=name,
        key_type=key_type,
        fingerprint=fingerprint,
        generated_at=generated_at or datetime.now(UTC),
        generated_by=f"{_resolve_user(user)}@{_resolve_hostname(hostname)}",
        serial=serial,
    )
