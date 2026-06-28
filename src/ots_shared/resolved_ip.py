# packages/ots-shared/src/ots_shared/resolved_ip.py

"""Resolved-IP sidecar read/write (multi-provider §5.1, BLOCKER 1).

Providers that **assign** private IPs automatically (DigitalOcean) cannot honor
an operator-pinned ``hosts.<role>.private_ip_address`` — the marker validator
rejects ``private_ip_*`` on those providers (§5.4a), so the assigned IP is
unknowable from ``otsinfra.yaml``. After ``lots deploy`` creates such a droplet
it reads back the assigned IP and persists it here, in a machine-written sidecar
``.trust/resolved-ips.yaml`` next to the marker. Downstream consumers that
resolve private IPs through :func:`ots_shared.ssh.env.get_host_ip` fall back to
this sidecar when the marker carries no IP, *before* failing loud.

Design constraints:

* **Provider-neutral, zero SDK.** Imports only ``yaml`` + the stdlib so it stays
  in the no-SDK plumbing tier (CI-enforced).
* **Machine-written, never operator-authored.** It does NOT mutate
  ``otsinfra.yaml`` (which §5.4a forbids ``private_ip_*`` on — writing it back
  there would be self-contradictory).
* **Keyed by hostname.** ``lots deploy`` operates per-hostname; the reader
  (:func:`get_host_ip`) reconstructs the ``<env>-<role>-<ordinal>`` hostname
  from the marker to look an entry up.

On Hetzner/UpCloud the marker still carries the pinned IP, so steps 1-3 of
``get_host_ip`` return first and this sidecar is never consulted.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# Sidecar location relative to the marker directory. ``.trust/`` already holds
# machine-managed deploy material (ssh keys, known_hosts); the resolved-IP map
# belongs alongside it rather than next to the operator-authored marker.
SIDECAR_RELPATH = Path(".trust") / "resolved-ips.yaml"

# Top-level key under which per-hostname entries live. Namespaced so the sidecar
# can grow other machine-resolved fields later without colliding.
_HOSTS_KEY = "hosts"
_IP_KEY = "private_ip"

_HEADER = (
    "# Machine-written by `lots deploy`. Do NOT edit by hand.\n"
    "# Records private IPs assigned by providers that allocate them\n"
    "# automatically (e.g. DigitalOcean); see multi-provider spec §5.1.\n"
)


def sidecar_path(marker_dir: Path) -> Path:
    """Return the sidecar path for a given marker directory."""
    return Path(marker_dir) / SIDECAR_RELPATH


def read_sidecar(path: Path) -> dict[str, Any]:
    """Load the sidecar mapping, returning ``{}`` for missing/empty/malformed.

    Fail-soft: a missing or unparsable sidecar yields the same "no entry"
    signal as an absent host, so callers fall through to their own hard
    ``SystemExit``-on-absence rather than crashing on a corrupt file.
    """
    import yaml

    path = Path(path)
    if not path.is_file():
        return {}
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    if not isinstance(loaded, dict):
        return {}
    return loaded


def get_resolved_ip(hostname: str, *, marker_dir: Path | None) -> str | None:
    """Return the persisted private IP for ``hostname``, or ``None``.

    ``marker_dir`` is the directory containing ``otsinfra.yaml`` (the sidecar
    lives at ``marker_dir/.trust/resolved-ips.yaml``). When ``None`` — no marker
    located — there is nothing to consult, so ``None`` is returned.
    """
    if marker_dir is None:
        return None
    data = read_sidecar(sidecar_path(marker_dir))
    hosts = data.get(_HOSTS_KEY)
    if not isinstance(hosts, dict):
        return None
    entry = hosts.get(hostname)
    if not isinstance(entry, dict):
        return None
    ip = entry.get(_IP_KEY)
    return ip if isinstance(ip, str) and ip else None


def write_resolved_ip(hostname: str, ip: str, *, marker_dir: Path) -> Path:
    """Persist ``ip`` for ``hostname`` into the sidecar (read-modify-write).

    Preserves any existing entries for other hostnames. Creates ``.trust/`` if
    absent. Returns the sidecar path written. Idempotent: re-recording the same
    IP rewrites the same content.
    """
    if not hostname:
        raise ValueError("hostname must be a non-empty str")
    if not ip:
        raise ValueError("ip must be a non-empty str")

    path = sidecar_path(marker_dir)
    data = read_sidecar(path)
    hosts = data.get(_HOSTS_KEY)
    if not isinstance(hosts, dict):
        hosts = {}
        data[_HOSTS_KEY] = hosts

    entry = hosts.get(hostname)
    if not isinstance(entry, dict):
        entry = {}
        hosts[hostname] = entry
    entry[_IP_KEY] = ip

    import yaml

    path.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(data, default_flow_style=False, sort_keys=True)
    path.write_text(_HEADER + body, encoding="utf-8")
    return path
