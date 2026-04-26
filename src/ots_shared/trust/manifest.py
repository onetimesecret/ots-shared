"""Plaintext-committable trust manifest at .trust/manifest.yaml."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from ._paths import PUBLIC_MODE


@dataclass
class ManifestEntry:
    name: str
    key_type: str
    fingerprint: str
    generated_at: datetime
    generated_by: str
    serial: int


def _entry_to_dict(entry: ManifestEntry) -> dict[str, Any]:
    return {
        "name": entry.name,
        "key_type": entry.key_type,
        "fingerprint": entry.fingerprint,
        "generated_at": entry.generated_at.astimezone(UTC).isoformat(),
        "generated_by": entry.generated_by,
        "serial": entry.serial,
    }


def _entry_from_dict(data: dict[str, Any]) -> ManifestEntry:
    return ManifestEntry(
        name=str(data["name"]),
        key_type=str(data["key_type"]),
        fingerprint=str(data["fingerprint"]),
        generated_at=datetime.fromisoformat(str(data["generated_at"])),
        generated_by=str(data["generated_by"]),
        serial=int(data["serial"]),
    )


@dataclass
class Manifest:
    entries: list[ManifestEntry] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> Manifest:
        if not path.exists():
            return cls()
        raw = yaml.safe_load(path.read_text()) or {}
        items = raw.get("entries", []) if isinstance(raw, dict) else []
        return cls(entries=[_entry_from_dict(item) for item in items])

    def save(self, path: Path) -> None:
        payload = {"entries": [_entry_to_dict(e) for e in self.entries]}
        path.write_text(yaml.safe_dump(payload, sort_keys=False))
        path.chmod(PUBLIC_MODE)

    def upsert(self, entry: ManifestEntry) -> None:
        for idx, existing in enumerate(self.entries):
            if existing.name == entry.name and existing.key_type == entry.key_type:
                self.entries[idx] = entry
                return
        self.entries.append(entry)

    def get(self, name: str, key_type: str) -> ManifestEntry | None:
        for entry in self.entries:
            if entry.name == name and entry.key_type == key_type:
                return entry
        return None

    def all(self) -> list[ManifestEntry]:
        return list(self.entries)
