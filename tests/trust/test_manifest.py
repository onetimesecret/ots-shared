"""Manifest load/save/upsert tests.

Spec refs: §15 (manifest fields and behavior), §73-76 (file modes).
"""

from __future__ import annotations

import stat
from datetime import UTC, datetime
from pathlib import Path

from ots_shared.trust.manifest import Manifest, ManifestEntry


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _make_entry(
    name: str = "host-a",
    key_type: str = "ssh",
    fingerprint: str = "SHA256:abc",
    serial: int = 1,
) -> ManifestEntry:
    """Construct a ManifestEntry with all required fields populated."""
    return ManifestEntry(
        name=name,
        key_type=key_type,  # type: ignore[arg-type]
        fingerprint=fingerprint,
        generated_at=datetime.now(tz=UTC),
        generated_by="tester@unit",
        serial=serial,
    )


def test_load_missing_returns_empty(tmp_path: Path) -> None:
    """§15: a missing manifest.yaml loads as an empty Manifest, not an error."""
    m = Manifest.load(tmp_path / "manifest.yaml")
    assert isinstance(m, Manifest)
    assert list(m.entries) == []


def test_save_round_trip(tmp_path: Path) -> None:
    """§15: save then load yields a Manifest with the same entries."""
    path = tmp_path / "manifest.yaml"
    m = Manifest()
    e1 = _make_entry(name="host-a", key_type="ssh", fingerprint="SHA256:aaa", serial=1)
    e2 = _make_entry(name="host-b", key_type="tls", fingerprint="SHA256:bbb", serial=2)
    m.upsert(e1)
    m.upsert(e2)
    m.save(path)

    loaded = Manifest.load(path)
    assert len(list(loaded.entries)) == 2

    by_key = {(e.name, e.key_type): e for e in loaded.entries}
    assert by_key[("host-a", "ssh")].fingerprint == "SHA256:aaa"
    assert by_key[("host-b", "tls")].fingerprint == "SHA256:bbb"
    assert by_key[("host-a", "ssh")].serial == 1
    assert by_key[("host-b", "tls")].serial == 2


def test_upsert_replaces_by_name_keytype(tmp_path: Path) -> None:
    """§15: upserting (name, key_type) replaces — no duplicates."""
    m = Manifest()
    m.upsert(_make_entry(name="host-a", key_type="ssh", fingerprint="SHA256:old", serial=1))
    m.upsert(_make_entry(name="host-a", key_type="ssh", fingerprint="SHA256:new", serial=2))

    entries = list(m.entries)
    assert len(entries) == 1, "same (name, key_type) must collapse to a single entry"
    assert entries[0].fingerprint == "SHA256:new"
    assert entries[0].serial == 2

    # Different key_type for the same name is a separate entry.
    m.upsert(_make_entry(name="host-a", key_type="wg", fingerprint="SHA256:wg", serial=3))
    assert len(list(m.entries)) == 2


def test_save_mode_0644(tmp_path: Path) -> None:
    """§73-76: saved manifest.yaml has mode 0644 (public/committable)."""
    path = tmp_path / "manifest.yaml"
    m = Manifest()
    m.upsert(_make_entry())
    m.save(path)

    assert _mode(path) == 0o644


def test_entry_carries_required_fields() -> None:
    """§15: ManifestEntry exposes name, key_type, fingerprint, generated_at,
    generated_by, and serial."""
    e = _make_entry()
    for field in ("name", "key_type", "fingerprint", "generated_at", "generated_by", "serial"):
        assert hasattr(e, field), f"ManifestEntry missing field {field!r}"

    # Light typing sanity.
    assert isinstance(e.name, str)
    assert isinstance(e.fingerprint, str)
    assert isinstance(e.generated_by, str)
    assert isinstance(e.serial, int)
