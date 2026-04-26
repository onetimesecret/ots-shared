# packages/ots-shared/tests/trust/test_cli_fingerprints_roundtrip.py

"""End-to-end fingerprint round-trip for AC #4.

Spec ref: AC #4 — "Fingerprints printed at generation match the output
of ``trust fingerprints`` invoked at any later time on the same
material."

The existing test_cli_fingerprints.test_fingerprints_match_manifest only
compares CLI output against the manifest. AC #4 is stricter: it pins the
fingerprints printed *by init at generation time* (init's stdout) to the
fingerprints printed by ``trust fingerprints`` later. A bug that wrote
the manifest correctly but printed a different value at generation, or vice
versa, would be invisible to the manifest-only check.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from ots_shared.trust.cli import app as trust_app
from ots_shared.trust.init_step import create_trust_material


def _run_app(app, args: list[str]) -> int | None:
    """Invoke a Cyclopts app and return its exit code."""
    with pytest.raises(SystemExit) as exc_info:
        app(args)
    return exc_info.value.code


def _make_marker(target: Path) -> None:
    (target / ".otsinfra.yaml").write_text(
        "environment: roundtrip\n"
        "created: '2026-04-25'\n"
        "hosts:\n"
        "  web:\n"
        "    private_ip_address: 10.0.0.21\n"
        "  db:\n"
        "    private_ip_address: 10.0.0.11\n"
    )


# Matches "SHA256:<base64>" — the fingerprint format compute_fingerprint emits.
# Restricted to the alphabet ssh-keygen / x509 hex / rfc4648 use; "=" allowed
# at end for padding.
_FP_RE = re.compile(r"SHA256:[A-Za-z0-9+/=]+")


def test_init_stdout_fingerprints_match_trust_fingerprints(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """AC #4: every fingerprint init prints at generation time appears
    in ``trust fingerprints`` output later.

    Captures init's stdout, parses the SHA256:... tokens it announced, then
    runs the inspection command and asserts containment. This is the
    runbook contract: an operator who copies a fingerprint from CI logs
    must be able to verify it with the inspection command later.
    """
    _make_marker(tmp_path)

    create_trust_material(tmp_path, hosts=["web", "db"])
    init_out = capsys.readouterr().out
    init_fingerprints = set(_FP_RE.findall(init_out))

    # Sanity: init must have announced at least the CA + 6 leaves
    # (web/{ssh,wg,tls}, db/{ssh,wg,tls}).
    assert len(init_fingerprints) >= 7, (
        f"init printed {len(init_fingerprints)} fingerprints; expected >=7. stdout was:\n{init_out}"
    )

    monkeypatch.chdir(tmp_path)
    rc = _run_app(trust_app, ["fingerprints"])
    cli_out = capsys.readouterr().out
    assert rc in (None, 0), f"trust fingerprints exited {rc!r}"

    cli_fingerprints = set(_FP_RE.findall(cli_out))

    # Every fingerprint init announced must surface in the CLI output.
    missing = init_fingerprints - cli_fingerprints
    assert not missing, (
        f"fingerprints printed at generation but missing from "
        f"`trust fingerprints`: {missing}\n"
        f"init stdout:\n{init_out}\n"
        f"cli stdout:\n{cli_out}"
    )
