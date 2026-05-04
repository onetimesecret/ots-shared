"""Fixtures for the trust material library and CLI tests.

All fixtures are scoped to ``tmp_path``. Nothing under the operator's real
``.trust/`` is touched.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from ots_shared.trust import (
    Manifest,
    ManifestEntry,
    generate_keypair,
    make_manifest_entry,
)
from ots_shared.trust.ca import CA, generate_ca

# Fixed roles for predictability in CLI tests — kept in sync with what
# ``ots_shared.ssh.env.DEFAULT_HOSTS`` produces (`web`, `db`) so a
# test that walks the marker sees the same set the fixture builds.
FIXTURE_ROLES = ("web", "db")


@pytest.fixture
def trust_dir(tmp_path: Path) -> Path:
    """Return the path the library tests treat as the operator ``.trust/`` root.

    Note: the directory is intentionally NOT created here. Tests verify that
    generation creates parent directories with the expected modes.
    """
    return tmp_path / ".trust"


@pytest.fixture
def ca(trust_dir: Path) -> CA:
    """A pre-generated CA under ``<trust_dir>/ca`` for tests that need one."""
    return generate_ca(trust_dir / "ca")


@pytest.fixture
def populated_trust_dir(tmp_path: Path) -> Path:
    """Build a complete .trust/ tree under tmp_path and return the checkout root.

    Layout produced (matches spec §54):

        tmp_path/
        ├── otsinfra.yaml          (marker; declares the fixture roles)
        └── .trust/
            ├── .gitignore
            ├── manifest.yaml
            ├── ca/{ca.crt, ca.key, serial}
            ├── hosts/{web,db}/{ssh, ssh.pub, cert.pem, key.pem, wg, wg.pub}
            └── socks/{ssh, ssh.pub}
    """
    checkout = tmp_path
    trust = checkout / ".trust"
    trust.mkdir(mode=0o700)

    # Marker — minimal but valid for the role-declaration tests.
    (checkout / "otsinfra.yaml").write_text(
        "env_name: test-fixture\n"
        "created: '2026-04-25'\n"
        "hosts:\n"
        "  web:\n"
        "    private_ip_address: 10.0.0.21\n"
        "  db:\n"
        "    private_ip_address: 10.0.0.11\n"
    )

    # CA.
    ca = generate_ca(trust / "ca")

    # Per-host keypairs across all three key types.
    manifest = Manifest()
    now = datetime.now(UTC)
    for role in FIXTURE_ROLES:
        host_dir = trust / "hosts" / role
        host_dir.mkdir(parents=True, mode=0o700, exist_ok=True)

        ssh_kp = generate_keypair("ssh", role, host_dir)
        wg_kp = generate_keypair("wg", role, host_dir)
        tls_kp = generate_keypair("tls", role, host_dir, ca=ca)

        for kp in (ssh_kp, wg_kp, tls_kp):
            manifest.upsert(
                make_manifest_entry(
                    name=kp.name,
                    key_type=kp.key_type,
                    fingerprint=kp.fingerprint,
                    serial=kp.serial or 0,
                    user="tester",
                    hostname="testhost",
                    generated_at=now,
                )
            )

    # SOCKS keypair — singleton trust material under .trust/socks/.
    # Mirrors what create_trust_material writes so the cloudinit
    # render path can read .trust/socks/{ssh, ssh.pub} directly.
    socks_dir = trust / "socks"
    socks_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
    socks_kp = generate_keypair("ssh", "socks", socks_dir)
    manifest.upsert(
        make_manifest_entry(
            name="socks",
            key_type="ssh",
            fingerprint=socks_kp.fingerprint,
            serial=socks_kp.serial or 0,
            user="tester",
            hostname="testhost",
            generated_at=now,
        )
    )
    manifest.save(trust / "manifest.yaml")

    # .gitignore — minimal pattern set matching spec §5/§56 so a CLI
    # path that reads it doesn't crash.
    (trust / ".gitignore").write_text(
        "# .trust/.gitignore — only *.age committable\n"
        "*\n"
        "!*.age\n"
        "!.gitignore\n"
        "!manifest.yaml\n"
        "!*.pub\n"
        "!*.crt\n"
        "!cert.pem\n"
        "!socks/\n"
    )

    return checkout


@pytest.fixture
def manifest_entries(populated_trust_dir: Path) -> list[ManifestEntry]:
    """Convenience accessor for the loaded manifest entries.

    Reads from the populated checkout's manifest directly; does not depend
    on the ``trust_dir`` fixture (which has different semantics for the
    library-level tests in this directory).
    """
    return Manifest.load(populated_trust_dir / ".trust" / "manifest.yaml").entries
