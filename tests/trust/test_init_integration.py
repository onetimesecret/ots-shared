# tests/trust/test_init_integration.py

"""Integration tests for ``init()`` materializing the .trust/ layout.

Each test traces to a clause in ``docs/specs/init-trust-material.md``. The
tests target the shared init sub-app at ``ots_shared.init`` because both
``lots init`` and ``pots init`` route through it (spec §49).

Some tests will fail until W2-init lands the trust wiring. That is
expected — the contract is the spec, not current code.
"""

from __future__ import annotations

import stat
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from cryptography import x509

from ots_shared.init import app

# ---- helpers ---------------------------------------------------------------


def _run_init(tmp_path: Path, *extra_args: str) -> int | None:
    """Invoke ``ots_shared.init.app`` against *tmp_path* and return the exit code.

    Mirrors the call shape used by ``tests/test_init.py``.
    """
    args = ["env1", "--directory", str(tmp_path), *extra_args]
    with pytest.raises(SystemExit) as exc_info:
        app(args)
    return exc_info.value.code


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _read_ca_fingerprint(trust: Path) -> str:
    """Read the CA fingerprint via the public Wave 1 API.

    Avoids depending on internal trust manifest structure for this kind of
    smoke check — we just need a stable identifier we can compare across runs.
    """
    from ots_shared.trust.ca import load_ca

    ca = load_ca(trust / "ca")
    return ca.fingerprint


def _mtimes_under(directory: Path) -> dict[Path, int]:
    """Return relative-path -> mtime_ns for every file under *directory*."""
    result: dict[Path, int] = {}
    for p in directory.rglob("*"):
        if p.is_file():
            result[p.relative_to(directory)] = p.stat().st_mtime_ns
    return result


# ---- AC #1, spec §4, §49-50 -----------------------------------------------


def test_first_run_materializes_full_layout(tmp_path: Path) -> None:
    """spec §4, §49-50: first init() under an empty checkout creates .trust/.

    Asserts:
      - ``.trust/`` exists at 0700.
      - ``.trust/ca/`` with CA cert + key + serial counter.
      - At least one host directory under ``.trust/hosts/`` carrying its
        ssh, ssh.pub, cert.pem, key.pem, wg, wg.pub set.
      - ``.trust/manifest.yaml`` and ``.trust/.gitignore``.
      - Private halves are 0600, public halves and CA cert are 0644.
    """
    rc = _run_init(tmp_path)
    assert rc in (0, None), f"init() exited non-zero: {rc}"

    trust = tmp_path / ".trust"
    assert trust.is_dir(), ".trust/ must exist after init()"
    assert _mode(trust) == 0o700, f".trust/ must be 0700, got {oct(_mode(trust))}"

    ca_dir = trust / "ca"
    assert ca_dir.is_dir() and _mode(ca_dir) == 0o700
    assert (ca_dir / "ca.crt").is_file()
    assert (ca_dir / "ca.key").is_file()
    assert (ca_dir / "serial").is_file()

    hosts_dir = trust / "hosts"
    assert hosts_dir.is_dir() and _mode(hosts_dir) == 0o700
    role_dirs = [p for p in hosts_dir.iterdir() if p.is_dir()]
    assert role_dirs, "init() must materialize at least one host role"

    for role in role_dirs:
        # spec §54: per-host artefacts.
        for name in ("ssh", "ssh.pub", "cert.pem", "key.pem", "wg", "wg.pub"):
            assert (role / name).is_file(), f"{role.name}/{name} missing"
        # spec §73-76: file modes.
        assert _mode(role / "ssh") == 0o600
        assert _mode(role / "ssh.pub") == 0o644
        assert _mode(role / "key.pem") == 0o600
        assert _mode(role / "cert.pem") == 0o644
        assert _mode(role / "wg") == 0o600
        assert _mode(role / "wg.pub") == 0o644

    assert (trust / "manifest.yaml").is_file()
    assert (trust / ".gitignore").is_file()


# ---- AC #1, spec §6 --------------------------------------------------------


def test_second_run_idempotent_no_regen(tmp_path: Path) -> None:
    """spec §6, AC #1: second init() leaves existing trust material untouched.

    Both the CA fingerprint and the per-file mtimes under .trust/ must be
    identical across the two invocations.
    """
    _run_init(tmp_path)
    trust = tmp_path / ".trust"
    fp_before = _read_ca_fingerprint(trust)
    mtimes_before = _mtimes_under(trust)

    _run_init(tmp_path)

    fp_after = _read_ca_fingerprint(trust)
    mtimes_after = _mtimes_under(trust)

    assert fp_after == fp_before, "CA fingerprint must not change on re-run"
    # Manifest may legitimately be re-saved (idempotent write), so we
    # focus the comparison on the keypair/cert files where regeneration
    # would be the bug. Use the same file set in both snapshots.
    key_files = {p for p in mtimes_before if p.parts and p.parts[0] in ("ca", "hosts")}
    for relpath in key_files:
        assert mtimes_after.get(relpath) == mtimes_before[relpath], (
            f"{relpath} mtime changed across idempotent runs — material regenerated"
        )


# ---- AC #2 ----------------------------------------------------------------


def test_add_host_materializes_only_new_entry(tmp_path: Path) -> None:
    """spec §6, AC #2: adding a host materializes only that host's files.

    After the first init() captures existing role mtimes, a new role is
    appended to the marker's ``hosts:`` block. The second init() must
    materialize only the new role; existing role mtimes must not change.
    """
    _run_init(tmp_path)
    trust = tmp_path / ".trust"
    pre_mtimes = _mtimes_under(trust / "hosts")
    pre_roles = {p.name for p in (trust / "hosts").iterdir() if p.is_dir()}

    # Inject a new host into the marker. We append a role we know was
    # not in the default set.
    new_role = "newhost"
    assert new_role not in pre_roles, "test fixture invalid: role already present"

    marker = tmp_path / ".otsinfra.yaml"
    text = marker.read_text()
    addition = f"  {new_role}:\n    private_ip_address: 10.0.0.99\n"
    if text.rstrip().endswith(":") or "hosts:" in text:
        # Append under the existing hosts: block.
        marker.write_text(text + addition)
    else:
        marker.write_text(text + "hosts:\n" + addition)

    _run_init(tmp_path)

    post_mtimes = _mtimes_under(trust / "hosts")
    post_roles = {p.name for p in (trust / "hosts").iterdir() if p.is_dir()}

    assert new_role in post_roles, "new role must be materialized"
    for old_role in pre_roles:
        for relpath, old_mtime in pre_mtimes.items():
            if relpath.parts[0] == old_role:
                assert post_mtimes.get(relpath) == old_mtime, (
                    f"{relpath} touched while adding {new_role} — existing "
                    "host material must remain untouched"
                )


# ---- spec §7 (--force) -----------------------------------------------------


def test_force_regenerates_and_prints_destruction_notice(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """spec §7: --force regenerates everything and prints a destruction notice.

    Asserts:
      - CA fingerprint differs across the two runs (something was regenerated).
      - stdout/stderr emitted under --force contains a recognisable warning
        about destruction or regeneration.
    """
    _run_init(tmp_path)
    trust = tmp_path / ".trust"
    fp_before = _read_ca_fingerprint(trust)
    capsys.readouterr()  # discard first-run stdout

    _run_init(tmp_path, "--force")

    fp_after = _read_ca_fingerprint(trust)
    captured = capsys.readouterr()
    blob = (captured.out + "\n" + captured.err).lower()

    assert fp_after != fp_before, "--force must regenerate the CA"
    assert any(token in blob for token in ("destroy", "destruct", "regenerat", "overwrit")), (
        f"--force must announce destruction/regeneration; "
        f"saw stdout={captured.out!r} stderr={captured.err!r}"
    )


# ---- spec §16, §112: --ca-days / --leaf-days ------------------------------


def test_ca_days_and_leaf_days_propagate(tmp_path: Path) -> None:
    """spec §16, §112: --ca-days and --leaf-days set certificate validity.

    Compares ``not_valid_after - not_valid_before`` against the requested
    duration with a one-day tolerance to absorb the small back-dating the
    builders apply.
    """
    _run_init(tmp_path, "--ca-days=2000", "--leaf-days=400")

    trust = tmp_path / ".trust"
    ca_cert = x509.load_pem_x509_certificate((trust / "ca" / "ca.crt").read_bytes())
    ca_lifetime = ca_cert.not_valid_after_utc - ca_cert.not_valid_before_utc
    assert abs(ca_lifetime - timedelta(days=2000)) < timedelta(days=1), (
        f"CA lifetime {ca_lifetime} not within 1d of 2000d"
    )

    # Pick any leaf and check its lifetime.
    role_dirs = [p for p in (trust / "hosts").iterdir() if p.is_dir()]
    assert role_dirs, "expected at least one host role"
    leaf = x509.load_pem_x509_certificate((role_dirs[0] / "cert.pem").read_bytes())
    leaf_lifetime = leaf.not_valid_after_utc - leaf.not_valid_before_utc
    assert abs(leaf_lifetime - timedelta(days=400)) < timedelta(days=1), (
        f"leaf lifetime {leaf_lifetime} not within 1d of 400d"
    )


def test_default_validity_when_flags_absent(tmp_path: Path) -> None:
    """spec §112: defaults are 1460 days for the CA and 730 days for leaves."""
    _run_init(tmp_path)

    trust = tmp_path / ".trust"
    ca_cert = x509.load_pem_x509_certificate((trust / "ca" / "ca.crt").read_bytes())
    ca_lifetime = ca_cert.not_valid_after_utc - ca_cert.not_valid_before_utc
    assert abs(ca_lifetime - timedelta(days=1460)) < timedelta(days=1), (
        f"default CA lifetime {ca_lifetime} not within 1d of 1460d"
    )

    role_dirs = [p for p in (trust / "hosts").iterdir() if p.is_dir()]
    assert role_dirs
    leaf = x509.load_pem_x509_certificate((role_dirs[0] / "cert.pem").read_bytes())
    leaf_lifetime = leaf.not_valid_after_utc - leaf.not_valid_before_utc
    assert abs(leaf_lifetime - timedelta(days=730)) < timedelta(days=1), (
        f"default leaf lifetime {leaf_lifetime} not within 1d of 730d"
    )


# ---- spec §5, §56: .trust/.gitignore --------------------------------------


def test_gitignore_blocks_cleartext_privates(tmp_path: Path) -> None:
    """spec §5, §56: .trust/.gitignore blocks cleartext private halves.

    Uses ``git check-ignore`` against a real ephemeral repo to enforce
    actual gitignore semantics (which fnmatch can only approximate).

    Required behaviour:
      - Cleartext privates (TLS ``key.pem``, raw ``ssh``, raw ``wg``,
        ``ca.key``) are ignored.
      - Age-encrypted blobs (``*.age``) are committable.
      - Spec §56 only mandates that ``*.age`` is committable, but the
        renderer also relies on plaintext public artefacts being
        committable, so we additionally check that the public halves
        (``*.pub``, ``*.crt``, ``manifest.yaml``) are not blocked.
    """
    import shutil
    import subprocess

    if shutil.which("git") is None:
        pytest.skip("git not available — cannot validate gitignore semantics")

    _run_init(tmp_path)

    trust = tmp_path / ".trust"

    # Initialise a throwaway repo at trust/ so .gitignore here is the
    # only one git considers. ``-c commit.gpgsign=false`` is not used —
    # we never commit, only call check-ignore.
    subprocess.run(
        ["git", "init", "-q"], cwd=trust, check=True, capture_output=True
    )

    def _is_ignored(rel: str) -> bool:
        # Materialise the candidate so check-ignore has something on disk
        # (some git versions consult the index). Use ``-f`` semantics
        # via ``--no-index`` to keep the test independent of staging.
        target = trust / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_bytes(b"")
        result = subprocess.run(
            ["git", "check-ignore", "--no-index", "-q", rel],
            cwd=trust,
            capture_output=True,
        )
        # Exit 0: ignored. Exit 1: not ignored. Anything else: error.
        if result.returncode not in (0, 1):
            pytest.fail(
                f"git check-ignore failed for {rel}: rc={result.returncode}, "
                f"stderr={result.stderr!r}"
            )
        return result.returncode == 0

    for cleartext in (
        "hosts/web/key.pem",
        "hosts/web/ssh",
        "hosts/web/wg",
        "ca/ca.key",
    ):
        assert _is_ignored(cleartext), (
            f".trust/.gitignore does not block cleartext private '{cleartext}'"
        )

    for committable in (
        "hosts/web/ssh.pub",
        "hosts/web/cert.pem",
        "hosts/web/wg.pub",
        "ca/ca.crt",
        "manifest.yaml",
        "hosts/web/key.pem.age",
    ):
        assert not _is_ignored(committable), (
            f"{committable} must remain committable; .trust/.gitignore blocks it"
        )


# Silence unused import warning in environments where datetime isn't used directly.
_ = datetime
