# deployments/containers/packages/ots-shared/src/ots_shared/ssh/env.py

"""OTS environment discovery, parsing, and host resolution.

Two file types mark an OTS environment directory:

``.otsinfra.yaml`` (current)
    Lightweight YAML marker and metadata. Its presence signals "this is
    an OTS environment directory". Direnv handles all env vars; this file
    carries structured metadata that doesn't belong in shell state.

``.otsinfra.env`` (legacy)
    KEY=VALUE targeting context for remote execution. Still supported for
    backward compatibility — walk-up discovery falls back to it when
    ``.otsinfra.yaml`` is not found.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

logger = logging.getLogger(__name__)

MARKER_FILENAME = ".otsinfra.yaml"
ENV_FILENAME = ".otsinfra.env"
_CONFIG_DIR_PREFIX = "config-v"
_CONFIG_SYMLINK = "config"


def _walk_up(filename: str, start: Path | None = None) -> Path | None:
    """Walk up from *start* looking for *filename*.

    Stops at the first directory containing .git or at the user's home
    directory — whichever is reached first. Returns None if not found.
    """
    current = (start or Path.cwd()).resolve()
    home = Path.home().resolve()

    while True:
        candidate = current / filename
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


def find_marker(start: Path | None = None) -> Path | None:
    """Walk up looking for ``.otsinfra.yaml``, then ``.otsinfra.env``.

    Prefers the YAML marker. Falls back to the legacy env file so
    existing environments continue to work during migration.
    """
    yaml_path = _walk_up(MARKER_FILENAME, start)
    if yaml_path is not None:
        return yaml_path
    return _walk_up(ENV_FILENAME, start)


def find_env_file(start: Path | None = None) -> Path | None:
    """Walk up from *start* looking for a .otsinfra.env file.

    Stops at the first directory containing .git or at the user's home
    directory — whichever is reached first. Returns None if not found.

    .. deprecated:: Use :func:`find_marker` for environment directory
       discovery. This function is retained for code that specifically
       needs the KEY=VALUE env file.
    """
    return _walk_up(ENV_FILENAME, start)


def load_marker(path: Path) -> dict:
    """Load an ``.otsinfra.yaml`` marker file.

    Returns a dict of the YAML contents. Uses a minimal safe loader
    that doesn't require PyYAML — the file format is simple enough
    for basic KEY: VALUE parsing. Falls back to PyYAML if available.
    """
    if not path.is_file():
        return {}

    text = path.read_text()

    # Try PyYAML first (preferred, handles all YAML)
    try:
        import yaml

        return yaml.safe_load(text) or {}
    except ImportError:
        pass

    # Minimal fallback: parse simple "key: value" lines
    result: dict = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        result[key.strip()] = value.strip()
    return result


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
        1. ``$OTS_CONFIG_DIR`` environment variable (explicit override).
        2. ``config/`` sibling to a ``.otsinfra.yaml`` or ``.otsinfra.env``
           marker (walk-up discovery confirms we're in an OTS environment).
        3. OTS_TAG from ``.otsinfra.env`` → versioned directory name
           (e.g. ``v0.24`` → ``config-v0.24``). Legacy path.

    Returns the directory path if it exists, None otherwise.
    """
    # 1. Explicit env var override
    env_config_dir = os.environ.get("OTS_CONFIG_DIR")
    if env_config_dir:
        p = Path(env_config_dir)
        if p.is_dir():
            logger.debug("Config dir from $OTS_CONFIG_DIR: %s", p)
            return p
        logger.warning("$OTS_CONFIG_DIR set but not a directory: %s", p)

    # 2. Walk up to find the environment marker, then check for sibling config/
    marker_path = find_marker(start)
    if marker_path is not None:
        config_dir = marker_path.parent / _CONFIG_SYMLINK
        if config_dir.is_dir():
            logger.debug("Config dir from %s sibling: %s", marker_path.name, config_dir)
            return config_dir

    # 3. Legacy: derive from OTS_TAG in .otsinfra.env
    env_path = find_env_file(start)
    if env_path is None:
        return None

    env_vars = load_env_file(env_path)
    tag = env_vars.get("OTS_TAG")
    if not tag:
        logger.debug("No OTS_TAG in %s", env_path)
        return None

    version = _tag_to_version(tag)
    if version is None:
        logger.warning("Cannot parse version from OTS_TAG=%s in %s", tag, env_path)
        return None

    config_dir = env_path.parent / f"{_CONFIG_DIR_PREFIX}{version}"
    if config_dir.is_dir():
        logger.debug("Config dir from %s: %s", env_path, config_dir)
        return config_dir

    logger.debug("Config dir does not exist: %s", config_dir)
    return None


def generate_env_template(
    host: str = "",
    tag: str = "",
    repository: str = "",
    rabbitmq_url: str = "",
    sidecar_host_id: str = "",
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
        "# --- SSH Targeting (required) ---",
        f"OTS_HOST={host}",
        "",
        "# --- Container Image (required for deploy ops) ---",
        f"OTS_TAG={tag}",
    ]
    if repository:
        lines.append(f"OTS_REPOSITORY={repository}")

    # Sidecar section
    lines.extend(
        [
            "",
            "# --- Sidecar Configuration (optional) ---",
            "# RABBITMQ_URL: Connection string for sidecar queue communication",
            "# Format: amqp://user:pass@host:port/vhost",
        ]
    )
    if rabbitmq_url:
        lines.append(f"RABBITMQ_URL={rabbitmq_url}")
    else:
        lines.append("# RABBITMQ_URL=amqp://onetimesecret:PASS@db-host:5672/onetimesecret")

    lines.extend(
        [
            "",
            "# SIDECAR_HOST_ID: Identifier for per-host queue binding",
            "# Defaults to socket.gethostname() if not set",
        ]
    )
    if sidecar_host_id:
        lines.append(f"SIDECAR_HOST_ID={sidecar_host_id}")
    else:
        lines.append("# SIDECAR_HOST_ID=")

    lines.append("")
    return "\n".join(lines)


def validate_env_file(path: Path) -> tuple[list[str], list[str]]:
    """Validate a .otsinfra.env file for completeness.

    Returns:
        Tuple of (warnings, errors) — empty lists if valid.
    """
    warnings: list[str] = []
    errors: list[str] = []

    if not path.is_file():
        errors.append(f"Environment file not found: {path}")
        return warnings, errors

    env_vars = load_env_file(path)

    # Required for SSH targeting
    if not env_vars.get("OTS_HOST"):
        errors.append("OTS_HOST is required for remote operations")

    # Required for container operations
    if not env_vars.get("OTS_TAG"):
        warnings.append("OTS_TAG not set — container operations may use defaults")

    # Optional but recommended for sidecar
    if not env_vars.get("RABBITMQ_URL"):
        warnings.append("RABBITMQ_URL not set — sidecar will use server defaults")

    # SIDECAR_HOST_ID is optional — falls back to socket.gethostname()

    return warnings, errors


def _tag_to_version(tag: str) -> str | None:
    """Extract major.minor version from a tag string.

    Accepts formats like ``v0.24``, ``v0.24.1``, ``0.24``, ``0.24.1``.
    Returns ``"0.24"`` (major.minor only) or None if unparseable.
    """
    m = re.match(r"v?(\d+\.\d+)", tag)
    return m.group(1) if m else None
