"""Paramiko SSHClient factory using ~/.ssh/config.

Creates SSH connections that honour the user's SSH config for Host,
User, Port, IdentityFile, and ProxyCommand settings.

Paramiko does not process ``Include`` directives, so we resolve them
recursively before parsing.
"""

from __future__ import annotations

import io
import logging
from glob import glob
from pathlib import Path

import paramiko

logger = logging.getLogger(__name__)

# Connection timeout in seconds
DEFAULT_TIMEOUT = 15


def _resolve_includes(config_path: Path, _seen: set[Path] | None = None) -> str:
    """Recursively expand ``Include`` directives in an SSH config file.

    Paramiko's SSHConfig parser silently ignores Include directives.
    This function reads the config, expands any Include lines (handling
    ``~`` expansion, relative paths, and glob patterns), and returns a
    single merged string suitable for ``SSHConfig.parse()``.
    """
    if _seen is None:
        _seen = set()

    resolved = config_path.resolve()
    if resolved in _seen:
        return ""
    _seen.add(resolved)

    if not config_path.is_file():
        return ""

    lines: list[str] = []
    for line in config_path.read_text().splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("include "):
            pattern = stripped.split(None, 1)[1].strip()
            # Strip surrounding quotes
            if len(pattern) >= 2 and pattern[0] == pattern[-1] and pattern[0] in ('"', "'"):
                pattern = pattern[1:-1]
            # Expand ~ and resolve relative paths against config dir
            pattern = str(Path(pattern).expanduser())
            if not Path(pattern).is_absolute():
                pattern = str(config_path.parent / pattern)
            for included_path in sorted(glob(pattern)):
                included = Path(included_path)
                if included.is_file():
                    lines.append(_resolve_includes(included, _seen))
        else:
            lines.append(line)

    return "\n".join(lines)


def ssh_connect(
    hostname: str,
    ssh_config_path: Path | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> paramiko.SSHClient:
    """Open an SSH connection to *hostname* using SSH config.

    Uses paramiko with RejectPolicy — the host must already be in
    known_hosts. Returns a connected paramiko.SSHClient.

    Raises:
        paramiko.SSHException: If connection or authentication fails.
    """
    config_path = ssh_config_path or Path.home() / ".ssh" / "config"

    # Parse SSH config with Include directives resolved
    ssh_config = paramiko.SSHConfig()
    if config_path.exists():
        merged = _resolve_includes(config_path)
        ssh_config.parse(io.StringIO(merged))

    host_config = ssh_config.lookup(hostname)

    # Build connection kwargs from SSH config
    connect_kwargs: dict = {
        "hostname": host_config.get("hostname", hostname),
        "timeout": timeout,
    }

    if "port" in host_config:
        connect_kwargs["port"] = int(host_config["port"])

    if "user" in host_config:
        connect_kwargs["username"] = host_config["user"]

    if "identityfile" in host_config:
        # SSH config may list multiple identity files; pass all that exist
        key_files = [
            str(Path(kf).expanduser())
            for kf in host_config["identityfile"]
            if Path(kf).expanduser().exists()
        ]
        if key_files:
            connect_kwargs["key_filename"] = key_files

    # ProxyCommand support
    proxy_cmd = host_config.get("proxycommand")
    if proxy_cmd:
        connect_kwargs["sock"] = paramiko.ProxyCommand(proxy_cmd)

    # Create client with RejectPolicy (security requirement)
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.RejectPolicy())

    # Load known hosts
    known_hosts = Path.home() / ".ssh" / "known_hosts"
    if known_hosts.exists():
        client.load_host_keys(str(known_hosts))
    client.load_system_host_keys()

    # Paramiko looks up non-standard ports as [host]:port in known_hosts,
    # but OpenSSH also accepts bare host entries as a fallback.  Mirror
    # that behaviour so existing known_hosts files work without needing
    # port-specific entries.
    port = connect_kwargs.get("port", 22)
    actual_host = connect_kwargs["hostname"]
    if port != 22:
        bracketed = f"[{actual_host}]:{port}"
        host_keys = client.get_host_keys()
        if bracketed not in host_keys and actual_host in host_keys:
            for key_type in host_keys[actual_host]:
                host_keys.add(bracketed, key_type, host_keys[actual_host][key_type])

    logger.info("SSH connecting to %s", connect_kwargs.get("hostname", hostname))
    try:
        client.connect(**connect_kwargs)
    except paramiko.PasswordRequiredException:
        # Key file is encrypted and no passphrase was provided.  Drop
        # key_filename so paramiko falls through to the SSH agent, which
        # typically has the decrypted key loaded (via AddKeysToAgent/UseKeychain).
        if "key_filename" in connect_kwargs:
            logger.debug("Encrypted key file, falling back to SSH agent")
            del connect_kwargs["key_filename"]
            client.connect(**connect_kwargs)
        else:
            raise
    return client
