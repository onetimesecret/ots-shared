""".otsinfra.env discovery, parsing, and host resolution.

The .otsinfra.env file provides targeting context for remote execution.
It is discovered by walking up from the current directory, stopping at
the repository root (.git) or the user's home directory.

Standard variables:
    OTS_HOST        Target host (SSH hostname or alias)
    OTS_REPOSITORY  Container image repository
    OTS_TAG         Release version tag (e.g. v0.24)
    OTS_IMAGE       Container image override (optional)
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

logger = logging.getLogger(__name__)

ENV_FILENAME = ".otsinfra.env"
_CONFIG_DIR_PREFIX = "config-v"
_CONFIG_SYMLINK = "config"


def find_env_file(start: Path | None = None) -> Path | None:
    """Walk up from *start* looking for a .otsinfra.env file.

    Stops at the first directory containing .git or at the user's home
    directory — whichever is reached first. Returns None if not found.
    """
    current = (start or Path.cwd()).resolve()
    home = Path.home().resolve()

    while True:
        candidate = current / ENV_FILENAME
        if candidate.is_file():
            return candidate

        # Stop at .git boundary
        if (current / ".git").exists():
            return None

        # Stop at home directory ceiling
        if current == home:
            return None

        parent = current.parent
        # Filesystem root — stop
        if parent == current:
            return None

        current = parent


def load_env_file(path: Path) -> dict[str, str]:
    """Parse a .otsinfra.env file into a dict.

    Format: KEY=VALUE lines. Blank lines and lines starting with # are
    ignored. Values are stripped of surrounding whitespace. Quoted values
    (single or double) are unquoted.
    """
    result: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # Strip matching quotes
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        result[key] = value
    return result


def resolve_host(host_flag: str | None = None) -> str | None:
    """Determine the target host using the resolution priority chain.

    Priority:
        1. Explicit --host flag value
        2. OTS_HOST environment variable
        3. OTS_HOST from .otsinfra.env (walk-up discovery)
        4. None (local execution)
    """
    # 1. Explicit flag
    if host_flag:
        logger.debug("Host from --host flag: %s", host_flag)
        return host_flag

    # 2. Environment variable
    env_host = os.environ.get("OTS_HOST")
    if env_host:
        logger.debug("Host from OTS_HOST env var: %s", env_host)
        return env_host

    # 3. Walk-up .otsinfra.env
    env_path = find_env_file()
    if env_path:
        env_vars = load_env_file(env_path)
        file_host = env_vars.get("OTS_HOST")
        if file_host:
            logger.info("Host from %s: %s", env_path, file_host)
            return file_host

    # 4. Local
    return None


def resolve_config_dir(start: Path | None = None) -> Path | None:
    """Resolve the current config directory for a jurisdiction.

    Resolution order:
        1. A ``config`` symlink or directory sibling to the .otsinfra.env file
           (stable pointer managed by the operator).
        2. OTS_TAG from .otsinfra.env → versioned directory name
           (e.g. ``v0.24`` → ``config-v0.24``).

    The config directory is expected to be a sibling of the env file.
    Returns the directory path if it exists, None otherwise.
    """
    env_path = find_env_file(start)
    if env_path is None:
        return None

    parent = env_path.parent

    # 1. Stable symlink convention: config -> config-v0.24
    symlink = parent / _CONFIG_SYMLINK
    if symlink.is_dir():
        logger.debug("Config dir from symlink %s: %s", symlink, symlink.resolve())
        return symlink

    # 2. Derive from OTS_TAG
    env_vars = load_env_file(env_path)
    tag = env_vars.get("OTS_TAG")
    if not tag:
        logger.debug("No OTS_TAG in %s", env_path)
        return None

    version = _tag_to_version(tag)
    if version is None:
        logger.warning("Cannot parse version from OTS_TAG=%s in %s", tag, env_path)
        return None

    config_dir = parent / f"{_CONFIG_DIR_PREFIX}{version}"
    if config_dir.is_dir():
        logger.debug("Config dir from %s: %s", env_path, config_dir)
        return config_dir

    logger.debug("Config dir does not exist: %s", config_dir)
    return None


def generate_env_template(
    host: str = "",
    tag: str = "",
    repository: str = "",
) -> str:
    """Generate a .otsinfra.env template with optional pre-filled values.

    Returns the file content as a string.
    """
    lines = [
        "# .otsinfra.env — targeting context for OTS remote operations",
        "#",
        "# Walk-up discovery: ots-containers commands search for this file",
        "# starting from the current directory upward to the repo root.",
        "",
        f"OTS_HOST={host}",
        f"OTS_TAG={tag}",
    ]
    if repository:
        lines.append(f"OTS_REPOSITORY={repository}")
    lines.append("")
    return "\n".join(lines)


def _tag_to_version(tag: str) -> str | None:
    """Extract major.minor version from a tag string.

    Accepts formats like ``v0.24``, ``v0.24.1``, ``0.24``, ``0.24.1``.
    Returns ``"0.24"`` (major.minor only) or None if unparseable.
    """
    m = re.match(r"v?(\d+\.\d+)", tag)
    return m.group(1) if m else None
