# tests/trust/test_manifest_corruption.py

"""Negative-path tests for manifest load.

Spec ref: §15 (manifest is the runbook reference). The user's documented
"fail loud, no guesswork" preference applies: malformed YAML must surface
as an error, not a silently-empty Manifest, because a silently-empty
manifest causes downstream commands (``lots trust fingerprints``) to claim
the tree is empty when in fact it carries valid material whose metadata
just won't load.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ots_shared.trust.manifest import Manifest


def test_load_malformed_yaml_raises(tmp_path: Path) -> None:
    """§15: manifest.yaml that is not valid YAML must raise, not return empty.

    Silent fallback would let a corrupted manifest masquerade as
    "no entries yet" — re-running init would then upsert fresh entries
    on top of orphaned material with stale fingerprints already on disk,
    producing a divergence between the manifest and the actual artefacts.
    """
    path = tmp_path / "manifest.yaml"
    # Tab + colon + dangling quote: yaml.safe_load rejects this with
    # yaml.YAMLError. We don't pin the exact exception class because the
    # contract is "loud failure", not a specific type.
    path.write_text(": not yaml [\n\t'unterminated\n")

    with pytest.raises(Exception) as excinfo:
        Manifest.load(path)

    # The raised error must point at YAML, not at "missing key" or similar
    # downstream NoneType failures from a silent fallback.
    assert "yaml" in type(excinfo.value).__module__.lower() or "yaml" in str(
        excinfo.value
    ).lower(), (
        f"expected a YAML-related error; got {type(excinfo.value).__name__}: "
        f"{excinfo.value}"
    )


def test_load_entry_missing_required_field_raises(tmp_path: Path) -> None:
    """§15: a manifest entry missing ``fingerprint`` must raise on load.

    Required fields (name, key_type, fingerprint, generated_at,
    generated_by, serial) are part of the manifest contract. A missing
    field on disk is corruption, not a default-fillable absence.
    """
    path = tmp_path / "manifest.yaml"
    path.write_text(
        "entries:\n"
        "  - name: web\n"
        "    key_type: ssh\n"
        # fingerprint deliberately missing
        "    generated_at: '2026-04-25T00:00:00+00:00'\n"
        "    generated_by: tester@unit\n"
        "    serial: 1\n"
    )

    with pytest.raises((KeyError, ValueError, TypeError)):
        Manifest.load(path)
