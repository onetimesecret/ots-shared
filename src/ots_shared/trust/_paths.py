"""Internal layout helpers for the .trust/ directory."""

from __future__ import annotations

import os
from pathlib import Path

DIR_MODE = 0o700
PRIVATE_MODE = 0o600
PUBLIC_MODE = 0o644


def trust_dir(out_dir: Path) -> Path:
    return out_dir


def ca_dir(out_dir: Path) -> Path:
    return out_dir / "ca"


def hosts_dir(out_dir: Path) -> Path:
    return out_dir / "hosts"


def host_dir(out_dir: Path, role: str) -> Path:
    return hosts_dir(out_dir) / role


def manifest_path(out_dir: Path) -> Path:
    return out_dir / "manifest.yaml"


def ensure_dir(path: Path) -> Path:
    """Create ``path`` (and any missing ancestors) with mode 0700.

    Ancestors that already exist are not chmodded — their owner picked the mode.
    Ancestors we create as a side effect of materializing ``path`` are chmodded
    to 0700 because they are part of the .trust/ tree.
    """
    to_create: list[Path] = []
    cursor = path
    while not cursor.exists():
        to_create.append(cursor)
        if cursor.parent == cursor:
            break
        cursor = cursor.parent
    path.mkdir(parents=True, exist_ok=True)
    for created in to_create:
        created.chmod(DIR_MODE)
    return path


def write_private(path: Path, data: bytes) -> Path:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, PRIVATE_MODE)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    path.chmod(PRIVATE_MODE)
    return path


def write_public(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    path.chmod(PUBLIC_MODE)
    return path
