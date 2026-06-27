# src/ots_shared/upcloud/server_defaults.py

"""Library helpers for resolving UpCloud server defaults from otsinfra.yaml.

Mirrors :mod:`ots_shared.hcloud.server_defaults`, reusing the truly
provider-neutral machinery (``MarkerField``, ``HostDefaults``, the
coercion/role-resolution helpers, ``marker_network_name``,
``format_traffic``) by importing it — the hcloud module is **not**
modified. The UpCloud-specific parts are:

  * a different :data:`MARKER_HOST_FIELDS` table — ``region`` (the atomic
    zone) instead of Hetzner's ``location``, no ``network_zone``, and the
    full ``private_ip_*`` set kept authoritative (UpCloud pins
    operator-chosen private IPs, so there is **no resolved-ips sidecar**);
  * :func:`load_cloud_init_user_data` with **no size cap**
    (``USER_DATA_LIMIT_BYTES = None``) — UpCloud documents no limit and the
    SDK passes ``user_data`` through verbatim; an oversized payload surfaces
    as ``USER_DATA_INVALID`` from the API, not a client-side reject;
  * :func:`get_server_or_exit` doing a list + client-side hostname filter
    (UpCloud has **no** get-by-name).
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

logger = logging.getLogger(__name__)

# UpCloud documents no user_data size limit and the SDK does no validation.
# None == "no client-side cap"; the API rejects oversized payloads with
# USER_DATA_INVALID, which the caller surfaces verbatim.
USER_DATA_LIMIT_BYTES: int | None = None


# ---------------------------------------------------------------------------
# Marker-backed flag table (UpCloud)
# ---------------------------------------------------------------------------
#
# One row == one marker key under hosts.<role>. ``server_type`` is either a
# named plan (e.g. "2xCPU-4GB") OR is paired with a custom core/mem at the
# CLI layer; ``image`` is a template title or UUID; ``region`` is the atomic
# zone id (NOT "location" — there is no network_zone for UpCloud). The
# private_ip_* set stays authoritative (no sidecar).

MARKER_HOST_FIELDS: tuple[MarkerField, ...] = (
    MarkerField("server_type", str, "--server-type"),
    MarkerField("image", str, "--image"),
    MarkerField("region", str, "--region"),
    MarkerField("private_ip_address", str, "--ip"),
    MarkerField("private_ip_cidr", str, "--ip-cidr"),
    MarkerField("private_ip_assignment_type", str, "--ip-assignment"),
    MarkerField("private_ip_formula", str, "--ip-formula"),
    # Schema-only profile selector (consumed by cloud-init generation).
    MarkerField("profile", str, "--profile"),
    # Representative non-str fields proving the coercion story.
    MarkerField("firewalls", list, "--firewall"),
    MarkerField("backup", bool, "--backup"),
)

_MARKER_FIELDS_BY_KEY: dict[str, MarkerField] = {f.key: f for f in MARKER_HOST_FIELDS}


def resolve_host_defaults(role: str | None, name: str) -> HostDefaults | None:
    """Resolve UpCloud server-create defaults from otsinfra.yaml.

    Same contract as the Hetzner resolver but validates against the UpCloud
    :data:`MARKER_HOST_FIELDS` table (``region`` not ``location``). Picks a
    host role from an explicit ``role`` or — when unset — by delegating to
    the canonical ``parse_hostname`` (via the shared ``_resolve_role``).

    Returns ``None`` when no marker file is found. Raises ``SystemExit`` —
    with distinct messages — when the marker has no ``hosts`` block, an
    explicit role is undeclared, hostname parsing fails, a known key has the
    wrong type, or an unknown scalar/list key appears (dict-valued keys are
    silently ignored as foreign-tool sections).
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
            f"Pass all of --server-type/--image/--region explicitly, or "
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


def marker_network_name_upcloud(marker_path: Path | None = None) -> str | None:
    """Return the top-level ``network.name`` from ``otsinfra.yaml``.

    Thin alias over the provider-neutral :func:`marker_network_name`
    re-exported for symmetry with the Hetzner module. UpCloud networks are
    zone-scoped (one IP range each) but the marker's ``network.name`` key is
    read identically.
    """
    return marker_network_name(marker_path)


def get_server_or_exit(manager: Any, name: str) -> Any:
    """Look up a server by hostname, exiting with a message if not found.

    UpCloud has **no** get-by-name endpoint, so we list summaries and filter
    client-side on ``hostname`` (mirroring the DigitalOcean pattern). A
    duplicate hostname is fail-loud — the operator must disambiguate by
    UUID. Returns the matching populated/summary ``Server`` object.
    """
    servers = manager.get_servers()
    matches = [s for s in servers if getattr(s, "hostname", None) == name]
    if not matches:
        raise SystemExit(f"Server '{name}' not found")
    if len(matches) > 1:
        uuids = ", ".join(getattr(s, "uuid", "?") for s in matches)
        raise SystemExit(
            f"Multiple servers named '{name}' ({uuids}); specify a UUID to disambiguate"
        )
    return matches[0]


@dataclass(frozen=True)
class CloudInitPayload:
    """Cloud-init payload prepared for the UpCloud API.

    ``user_data`` is the string handed to ``Server(user_data=...)``;
    ``metadata=True`` must accompany it at create time (the API defaults
    metadata off). UpCloud applies no size cap, so ``raw_size`` and
    ``payload_size`` are equal — the field is passed through verbatim.
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

    Returns ``None`` when neither source is given. Raises ``SystemExit``
    with a human-readable message for bad input, missing files, or non-zero
    command exits.

    UpCloud imposes **no client-side size cap** (``max_bytes`` defaults to
    ``None`` — accept any size). An oversized payload is the API's call:
    it returns ``USER_DATA_INVALID``, which the command surfaces verbatim.
    ``max_bytes`` is accepted only so a caller can opt into a soft warning;
    even then nothing is rejected here.
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

    # No reject gate. A non-None max_bytes is only ever a soft warning —
    # UpCloud (not us) decides whether the payload is too large.
    if max_bytes is not None and raw_size > max_bytes:
        logger.warning(
            "cloud-init payload is %d bytes (> soft limit %d); UpCloud has no "
            "documented cap, so this is sent as-is and may be rejected with "
            "USER_DATA_INVALID.",
            raw_size,
            max_bytes,
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
    "get_server_or_exit",
    "load_cloud_init_user_data",
    "marker_network_name",
    "marker_network_name_upcloud",
    "resolve_host_defaults",
]
