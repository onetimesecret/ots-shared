"""SSH remote execution support for OTS operations tools.

Public API:
    - Environment: find_env_file, load_env_file, resolve_config_dir, resolve_host
    - Executor: Result, CommandError, Executor, LocalExecutor, SSHExecutor, is_remote
    - Connection: ssh_connect
"""

from .env import (
    find_env_file,
    generate_env_template,
    load_env_file,
    resolve_config_dir,
    resolve_host,
)
from .executor import (
    SSH_DEFAULT_TIMEOUT,
    CommandError,
    Executor,
    LocalExecutor,
    Result,
    SSHExecutor,
    is_remote,
)

__all__ = [
    "find_env_file",
    "generate_env_template",
    "load_env_file",
    "resolve_config_dir",
    "resolve_host",
    "SSH_DEFAULT_TIMEOUT",
    "CommandError",
    "Executor",
    "LocalExecutor",
    "Result",
    "SSHExecutor",
    "is_remote",
    "ssh_connect",
]


def ssh_connect(
    hostname: str,
    ssh_config_path: object | None = None,
    timeout: int = 15,
) -> object:
    """Open an SSH connection. Deferred import to avoid requiring paramiko."""
    from .connection import ssh_connect as _ssh_connect

    return _ssh_connect(hostname, ssh_config_path=ssh_config_path, timeout=timeout)  # type: ignore[arg-type]
