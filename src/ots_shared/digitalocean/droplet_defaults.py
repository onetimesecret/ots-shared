# src/ots_shared/digitalocean/droplet_defaults.py

"""Library helpers for resolving DigitalOcean droplet defaults from otsinfra.yaml.

Mirrors :mod:`ots_shared.upcloud.server_defaults`, reusing the truly
provider-neutral machinery (``MarkerField``, ``HostDefaults``, the
coercion/role-resolution helpers, ``marker_network_name``, ``format_traffic``)
by importing it from the hcloud module — which is **not** modified. The
DigitalOcean-specific parts are:

  * a different :data:`MARKER_HOST_FIELDS` table — ``region`` (the atomic
    region/zone) instead of Hetzner's ``location``, no ``network_zone``, and
    **no** ``private_ip_*`` fields: DO assigns the VPC private IP sequentially
    and cannot honor a pinned address (multi-provider.md §5.1/§5.4a). A marker
    that declares ``private_ip_*`` is **fail-loud**, not silently stripped;
  * :func:`load_cloud_init_user_data` with a **64 KiB hard cap**
    (``USER_DATA_LIMIT_BYTES``) — DO documents a 64 KiB user_data ceiling
    (Hetzner's is 32 KiB; UpCloud has none), enforced client-side so an
    over-limit payload fails fast (exit 65) instead of after the API round-trip;
  * :func:`get_droplet_or_exit` doing a paginated list + client-side name filter
    (DO has **no** get-by-name).
"""

from __future__ import annotations

import logging
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Reuse provider-neutral pieces from hcloud WITHOUT modifying that module.
from ots_shared.hcloud.server_defaults import (
    HostDefaults,
    MarkerField,
    MarkerValue,
    _coerce_marker_value,
    _resolve_role,
    format_traffic,
    marker_network_name,
)

from .paginate import find_by_name, list_all

logger = logging.getLogger(__name__)

# DigitalOcean documents a 64 KiB user_data ceiling. Enforced client-side so an
# over-limit payload fails fast rather than after a wasted create round-trip.
USER_DATA_LIMIT_BYTES: int = 64 * 1024

# Marker keys that pin a private IP. DO assigns these automatically and cannot
# honor them, so their presence is a hard error (multi-provider.md §5.4a).
_PINNED_IP_KEYS = (
    "private_ip_address",
    "private_ip_cidr",
    "private_ip_assignment_type",
    "private_ip_formula",
)


# ---------------------------------------------------------------------------
# Marker-backed flag table (DigitalOcean)
# ---------------------------------------------------------------------------
#
# One row == one marker key under hosts.<role>. ``server_type`` is a size slug
# (e.g. "s-2vcpu-4gb"); ``image`` is an image slug or numeric snapshot id;
# ``region`` is the atomic region slug (NOT "location" — there is no
# network_zone for DO). The private_ip_* set is deliberately ABSENT: pinned
# private IPs are unsupported and rejected up front (see _reject_pinned_ip).

MARKER_HOST_FIELDS: tuple[MarkerField, ...] = (
    MarkerField("server_type", str, "--size"),
    MarkerField("image", str, "--image"),
    MarkerField("region", str, "--region"),
    # Schema-only profile selector (consumed by cloud-init generation).
    MarkerField("profile", str, "--profile"),
    # Representative non-str fields proving the coercion story.
    MarkerField("firewalls", list, "--firewall"),
    MarkerField("backup", bool, "--backup"),
)

_MARKER_FIELDS_BY_KEY: dict[str, MarkerField] = {f.key: f for f in MARKER_HOST_FIELDS}


def _reject_pinned_ip(marker_path: Path, role: str, host: dict[str, Any]) -> None:
    """Fail loud if a host declares any ``private_ip_*`` field (§5.4a).

    Operator intent ("this host MUST have this IP") is unsatisfiable on DO, and
    papering over it would corrupt the WireGuard/confext IP plan downstream, so
    this is a hard error with a remediation hint rather than a silent strip.
    """
    present = [k for k in _PINNED_IP_KEYS if k in host]
    if present:
        raise SystemExit(
            f"{marker_path}: hosts.{role}.{present[0]} is set but the digitalocean "
            f"provider assigns private IPs automatically and cannot honor a pinned "
            f"address. Remove private_ip_* from otsinfra.yaml, or deploy this host "
            f"on hetzner/upcloud."
        )


def resolve_host_defaults(role: str | None, name: str) -> HostDefaults | None:
    """Resolve DigitalOcean droplet-create defaults from otsinfra.yaml.

    Same contract as the Hetzner/UpCloud resolvers but validates against the DO
    :data:`MARKER_HOST_FIELDS` table (``region`` not ``location``) and rejects
    ``private_ip_*`` fields (§5.4a). Picks a host role from an explicit ``role``
    or — when unset — by delegating to the canonical ``parse_hostname`` (via the
    shared ``_resolve_role``).

    Returns ``None`` when no marker file is found. Raises ``SystemExit`` — with
    distinct messages — when the marker has no ``hosts`` block, an explicit role
    is undeclared, hostname parsing fails, a pinned-IP field is present, a known
    key has the wrong type, or an unknown scalar/list key appears (dict-valued
    keys are silently ignored as foreign-tool sections).
    """
    try:
        from ots_shared.ssh.env import find_marker, load_marker
    except ImportError:
        return None

    marker_path = find_marker()
    if marker_path is None:
        return None

    marker = load_marker(marker_path)
    if not isinstance(marker, dict) or "hosts" not in marker:
        raise SystemExit(
            f"{marker_path}: no 'hosts' block — cannot infer defaults. "
            f"Pass all of --size/--image/--region explicitly, or "
            f"add a hosts: section."
        )

    hosts = marker["hosts"]
    if not isinstance(hosts, dict) or not hosts:
        raise SystemExit(f"{marker_path}: 'hosts' block is empty — cannot infer defaults.")

    available = sorted(hosts.keys())
    resolved_role = _resolve_role(role, name, hosts, available, marker, marker_path)

    host = hosts.get(resolved_role, {})
    if not isinstance(host, dict):
        raise SystemExit(
            f"{marker_path}: hosts.{resolved_role} must be a mapping, got {type(host).__name__}."
        )

    # Pinned-IP fields are a hard error before the generic unknown-key check so
    # the operator gets the targeted remediation hint, not "unknown key".
    _reject_pinned_ip(marker_path, resolved_role, host)

    # Fail loud on unknown scalar/list keys (catches the typo case). Dict
    # values are foreign-tool sections (rots' unce:/caddy:) — ignored.
    allowed = set(_MARKER_FIELDS_BY_KEY)
    unknown = sorted(k for k, v in host.items() if k not in allowed and not isinstance(v, dict))
    if unknown:
        raise SystemExit(
            f"{marker_path}: hosts.{resolved_role} has unknown key(s): "
            f"{unknown}. Allowed: {sorted(allowed)}. "
            f"(Dict-valued keys are ignored as foreign-tool sections.)"
        )

    # Seed list-kind fields with [] so consumers never see None for them.
    values: dict[str, MarkerValue] = {f.key: [] for f in MARKER_HOST_FIELDS if f.kind is list}
    for key, raw in host.items():
        field = _MARKER_FIELDS_BY_KEY.get(key)
        if field is None:
            continue  # dict-valued foreign-tool section, skipped above.
        values[key] = _coerce_marker_value(field, raw, marker_path=marker_path, role=resolved_role)

    return HostDefaults(values=values, role=resolved_role, marker_path=marker_path)


def get_droplet_or_exit(client: Any, name: str) -> dict[str, Any]:
    """Look up a droplet by name, exiting with a message if not found.

    DO has **no** get-by-name endpoint, so we pass the server-side ``name=``
    filter and additionally filter client-side (the API filter is a prefix-free
    exact match, but we never trust it as unique). A duplicate name is fail-loud
    — the operator must disambiguate by id. Returns the matching droplet dict.
    """
    droplets = list_all(
        lambda **kw: client.droplets.list(name=name, **kw),
        "droplets",
    )
    return find_by_name(droplets, name, kind="droplet")


@dataclass(frozen=True)
class CloudInitPayload:
    """Cloud-init payload prepared for the DigitalOcean API.

    ``user_data`` is the string handed to ``droplets.create({"user_data": ...})``.
    DO enforces a 64 KiB ceiling, checked here, so ``raw_size`` and
    ``payload_size`` are equal (no transform); the field records both for parity
    with the other providers' payload shape.
    """

    user_data: str
    raw_size: int
    payload_size: int


def load_cloud_init_user_data(
    path: Path | None,
    cmd: str | None = None,
    *,
    max_bytes: int | None = USER_DATA_LIMIT_BYTES,
) -> CloudInitPayload | None:
    """Load cloud-init YAML from a file or shell command.

    Returns ``None`` when neither source is given. Raises ``SystemExit`` with a
    human-readable message for bad input, missing files, non-zero command exits,
    or a payload over ``max_bytes`` (the 64 KiB DO ceiling — a client-side
    reject, exit 65, so it fails before the create round-trip).
    """
    if path is not None and cmd is not None:
        raise SystemExit("--cloud-init and --cloud-init-cmd are mutually exclusive")

    if path is not None:
        if not path.exists():
            raise SystemExit(f"Cloud-init file not found: {path}")
        content = path.read_text()
        source = f"file: {path}"
    elif cmd is not None:
        print(f"Running cloud-init command: {cmd}", file=sys.stderr)
        # cmd is operator-supplied; shell=True lets callers use pipes.
        proc = subprocess.run(
            cmd,
            shell=True,  # noqa: S602
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            if proc.stderr:
                print(proc.stderr, file=sys.stderr)
            raise SystemExit(f"Cloud-init command failed (exit {proc.returncode}): {cmd}")
        content = proc.stdout
        if not content.strip():
            raise SystemExit("Cloud-init command produced no output")
        source = "command"
    else:
        return None

    raw_bytes = content.encode("utf-8")
    raw_size = len(raw_bytes)

    print(f"Loaded cloud-init from {source} ({raw_size} bytes)", file=sys.stderr)

    if max_bytes is not None and raw_size > max_bytes:
        raise SystemExit(
            f"Cloud-init payload exceeds {max_bytes // 1024} KiB limit ({raw_size} bytes)"
        )

    return CloudInitPayload(user_data=content, raw_size=raw_size, payload_size=raw_size)


__all__ = [
    "MARKER_HOST_FIELDS",
    "USER_DATA_LIMIT_BYTES",
    "CloudInitPayload",
    "HostDefaults",
    "MarkerField",
    "MarkerValue",
    "format_traffic",
    "get_droplet_or_exit",
    "load_cloud_init_user_data",
    "marker_network_name",
    "resolve_host_defaults",
]
