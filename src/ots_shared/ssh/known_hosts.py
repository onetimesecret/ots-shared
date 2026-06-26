# src/ots_shared/ssh/known_hosts.py

"""Best-effort pruning of stale host-key entries from a known_hosts file.

When a cloud instance is destroyed and recreated, the provider's sshd
keygens a fresh host key. Under ``StrictHostKeyChecking accept-new`` the
operator's per-environment ``known_hosts`` still carries the *old* key, so
the next connection aborts with REMOTE HOST IDENTIFICATION HAS CHANGED
(``accept-new`` silently trusts *unknown* hosts but refuses *changed* ones).
Removing the stale entry at destroy time lets the eventual recreate re-TOFU
cleanly.

Pruning is teardown cleanup, never a precondition of the destroy, so every
failure mode here is a no-op rather than an exception: no ``SSH_CONFIG``,
``ssh`` or ``ssh-keygen`` absent, the known_hosts file missing, or no
matching line. The host key is resolved via ``ssh -G`` so the entry removed
is exactly the ``[host]:port`` (or bare ``host``) key OpenSSH would have
stored for this connection — not the bare config alias, which never matches
a stored entry for a ProxyJump'd, non-default-port host.
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# ``ssh -G`` and ``ssh-keygen -R`` are local-only (config parse / file
# rewrite); cap them so a pathological config or filesystem can't hang a
# teardown. The ssh -G timeout mirrors pots.ssh.routing._run_ssh_g.
_SSH_G_TIMEOUT = 5
_KEYGEN_TIMEOUT = 5


def _run_ssh_g(hostname: str, ssh_config: Path | None) -> dict[str, tuple[str, ...]] | None:
    """Run ``ssh -G <hostname>`` and parse stdout into a keyword dict.

    Returns None if ssh is unavailable, errors, or its output can't be
    parsed. Keys are lowercased; repeated keywords accumulate in order (one
    ``keyword value`` per line). Mirrors the parsing contract of
    ``pots.ssh.routing._run_ssh_g`` without taking a cross-package
    dependency (the DAG keeps lots/pots off each other).
    """
    argv = ["ssh"]
    if ssh_config is not None:
        argv.extend(["-F", str(ssh_config)])
    argv.extend(["-G", hostname])
    try:
        result = subprocess.run(
            argv, capture_output=True, text=True, timeout=_SSH_G_TIMEOUT, check=False
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    staging: dict[str, list[str]] = {}
    for raw in result.stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        key, _, value = line.partition(" ")
        staging.setdefault(key.lower(), []).append(value)
    return {k: tuple(v) for k, v in staging.items()}


def _known_hosts_target(parsed: dict[str, tuple[str, ...]]) -> str | None:
    """Build the known_hosts lookup key OpenSSH would store for this host.

    ``[hostname]:port`` for a non-default port, bare ``hostname`` for 22 —
    matching how OpenSSH writes (and looks up) the entry. Returns None when
    ssh -G yielded no resolvable hostname.
    """
    host_values = parsed.get("hostname")
    if not host_values or not host_values[0]:
        return None
    host = host_values[0]
    port_values = parsed.get("port")
    port = port_values[0] if port_values else "22"
    if port and port != "22":
        return f"[{host}]:{port}"
    return host


def _known_hosts_files(parsed: dict[str, tuple[str, ...]]) -> list[Path]:
    """Resolve *existing* ``UserKnownHostsFile`` paths from ssh -G output.

    ssh -G emits all configured paths on one line; the literal ``none``
    means "no file". Missing files are dropped here because ``ssh-keygen
    -R`` treats a missing ``-f`` path as a fatal error — filtering first is
    what keeps the prune a clean no-op when the file isn't there yet.
    """
    raw_values = parsed.get("userknownhostsfile")
    if not raw_values:
        return []
    files: list[Path] = []
    for token in raw_values[0].split():
        if token.lower() == "none":
            continue
        path = Path(token).expanduser()
        if path.is_file():
            files.append(path)
    return files


def _run_keygen_remove(target: str, known_hosts: Path) -> bool:
    """Run ``ssh-keygen -R`` to drop ``target`` from ``known_hosts``.

    Returns True if ssh-keygen ran — a no-match is a successful no-op (exit
    0) — and False only if the ssh-keygen binary is absent or timed out.
    ``check=False`` keeps a non-zero exit from raising; the caller has
    already confirmed the file exists, which is the one input ssh-keygen -R
    rejects fatally.
    """
    try:
        subprocess.run(
            ["ssh-keygen", "-R", target, "-f", str(known_hosts)],
            capture_output=True,
            text=True,
            timeout=_KEYGEN_TIMEOUT,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False
    return True


def prune_known_hosts(hostname: str, *, ssh_config: Path | str | None = None) -> list[Path]:
    """Remove ``hostname``'s host-key entries from the env known_hosts file(s).

    Resolves the connect target (``HostName``/``Port``) and
    ``UserKnownHostsFile`` via ``ssh -G`` — honoring ``ssh_config`` (or
    ``$SSH_CONFIG`` when omitted) so a per-environment config resolves
    against its own file — then runs ``ssh-keygen -R`` for the
    ``[host]:port`` (or bare ``host``) key ssh would have stored.

    Returns the known_hosts files ssh-keygen was run against (those that
    existed); empty when there is nothing to do. Never raises — see the
    module docstring.
    """
    resolved_config = ssh_config if ssh_config is not None else os.environ.get("SSH_CONFIG")
    config_path = Path(resolved_config).expanduser() if resolved_config else None

    parsed = _run_ssh_g(hostname, config_path)
    if parsed is None:
        logger.debug("known_hosts prune skipped for %r: ssh -G unavailable", hostname)
        return []

    target = _known_hosts_target(parsed)
    if target is None:
        logger.debug("known_hosts prune skipped for %r: no resolvable hostname", hostname)
        return []

    pruned: list[Path] = []
    for known_hosts in _known_hosts_files(parsed):
        if not _run_keygen_remove(target, known_hosts):
            logger.debug("known_hosts prune halted for %r: ssh-keygen unavailable", hostname)
            break
        pruned.append(known_hosts)
    return pruned
