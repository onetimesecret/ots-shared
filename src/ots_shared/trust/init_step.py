"""Trust material init orchestrator.

Materializes ``.trust/`` under the operator checkout. The Wave-1 trust
primitives (``generate_ca``, ``generate_keypair``) are individually
idempotent; this module composes them with the cross-cutting concerns
the spec requires: a flock around the whole generation path, a strict
``.trust/.gitignore``, a manifest update scoped to newly-generated
material only, and stable fingerprint output.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from . import TRUST_DIRNAME, generate_keypair, make_manifest_entry, trust_flock
from ._paths import (
    DIR_MODE,
    PUBLIC_MODE,
    ca_dir,
    ensure_dir,
    host_dir,
    hosts_dir,
    manifest_path,
    trust_dir,
)
from .ca import generate_ca, next_serial
from .manifest import Manifest

_TRUST_GITIGNORE = """\
# Cleartext private halves never enter version control.
# The age-sealed ``*.age`` form is the only committable shape.
*
!*.age
!*.pub
!*.crt
!cert.pem
!manifest.yaml
!.gitignore
!ca/
!hosts/
!hosts/*/
!socks/
"""


def _write_trust_gitignore(trust_root: Path) -> None:
    path = trust_root / ".gitignore"
    path.write_text(_TRUST_GITIGNORE)
    path.chmod(PUBLIC_MODE)


def _ssh_files_present(role_dir: Path) -> bool:
    return (role_dir / "ssh").exists() and (role_dir / "ssh.pub").exists()


def _wg_files_present(role_dir: Path) -> bool:
    return (role_dir / "wg").exists() and (role_dir / "wg.pub").exists()


def _tls_files_present(role_dir: Path) -> bool:
    return (role_dir / "key.pem").exists() and (role_dir / "cert.pem").exists()


def create_trust_material(
    target: Path,
    *,
    hosts: list[str],
    force: bool = False,
    ca_days: int = 1460,
    leaf_days: int = 730,
) -> Path:
    """Materialize ``.trust/`` on disk under ``target``. Returns the ``.trust/`` root.

    The orchestrator is incrementally idempotent (spec §6, AC #2): existing
    on-disk material is left untouched and only missing entries are generated.
    Fingerprint output is emitted for material generated *this run* so callers
    can grep stable lines from CI logs.

    With ``force=True`` (spec §7), an explicit destruction notice is printed
    and ``.trust/`` is removed before regeneration.
    """
    trust_root = trust_dir(target / TRUST_DIRNAME)

    with trust_flock(target):
        if force and trust_root.exists():
            # Explicit destruction notice (spec §7) before any irreversible
            # action. Inside the flock so a concurrent --force run cannot
            # rmtree another worker's in-flight ``.trust/`` (AC #5).
            print(f"--force: destroying existing {trust_root} and regenerating")
            shutil.rmtree(trust_root)
        ensure_dir(trust_root)
        trust_root.chmod(DIR_MODE)

        ca_path = ca_dir(trust_root)
        ca_existed = (ca_path / "ca.crt").exists() and (ca_path / "ca.key").exists()
        ca = generate_ca(ca_path, days=ca_days)
        ensure_dir(hosts_dir(trust_root))

        manifest = Manifest.load(manifest_path(trust_root))

        if not ca_existed:
            manifest.upsert(
                make_manifest_entry(
                    name="ca",
                    key_type="ca",
                    fingerprint=ca.fingerprint,
                    serial=1,
                )
            )
            print(f"ca {ca.fingerprint}")

        for role in hosts:
            role_dir = ensure_dir(host_dir(trust_root, role))

            if not _ssh_files_present(role_dir):
                kp = generate_keypair("ssh", role, role_dir)
                manifest.upsert(
                    make_manifest_entry(
                        name=role,
                        key_type="ssh",
                        fingerprint=kp.fingerprint,
                        serial=kp.serial or 0,
                    )
                )
                print(f"{role} ssh {kp.fingerprint}")

            if not _wg_files_present(role_dir):
                # Spec §113: WG inherits the per-CA serial counter for
                # timeline accounting; WG has no native serial concept.
                # Wave 1 ``generate_keypair`` ignores ``ca=`` for wg (see
                # ``test_wg_ignores_ca_param``), so the orchestrator allocates
                # the timeline serial directly via ``next_serial(ca)``.
                kp = generate_keypair("wg", role, role_dir)
                wg_serial = next_serial(ca)
                manifest.upsert(
                    make_manifest_entry(
                        name=role,
                        key_type="wg",
                        fingerprint=kp.fingerprint,
                        serial=wg_serial,
                    )
                )
                print(f"{role} wg {kp.fingerprint}")

            if not _tls_files_present(role_dir):
                kp = generate_keypair("tls", role, role_dir, ca=ca, leaf_days=leaf_days)
                manifest.upsert(
                    make_manifest_entry(
                        name=role,
                        key_type="tls",
                        fingerprint=kp.fingerprint,
                        serial=kp.serial or 0,
                    )
                )
                print(f"{role} tls {kp.fingerprint}")

        # SOCKS keypair (singleton, not per-role). The db consumes the
        # private half over the SOCKS-over-SSH egress tunnel; the web
        # peer authorises the public half on the ``socks-proxy`` user.
        # Lives at the trust-root level rather than under ``hosts/``
        # because it represents one shared identity, not per-host
        # material. Cloud-init carries the private half on first boot —
        # the documented exception to "public-only payload" (spec §122-134),
        # since db has no outbound IPv4 until the tunnel is up. Persisting
        # it here means re-renders do not regenerate (AC #1).
        socks_dir = ensure_dir(trust_root / "socks")
        if not _ssh_files_present(socks_dir):
            kp = generate_keypair("ssh", "socks", socks_dir)
            manifest.upsert(
                make_manifest_entry(
                    name="socks",
                    key_type="ssh",
                    fingerprint=kp.fingerprint,
                    serial=kp.serial or 0,
                )
            )
            print(f"socks ssh {kp.fingerprint}")

        manifest.save(manifest_path(trust_root))
        _write_trust_gitignore(trust_root)

    return trust_root


__all__ = ["create_trust_material", "TRUST_DIRNAME"]
