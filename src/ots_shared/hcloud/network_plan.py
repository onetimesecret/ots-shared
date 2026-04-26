# src/ots_shared/hcloud/network_plan.py

"""Pure logic for the ``network ensure`` reconciler.

This module has no I/O dependencies — no Hetzner API calls, no file
reads. It accepts an already-loaded ``.otsinfra.yaml`` dict, validates
the schema, derives per-profile ``/24`` subnets from host private IPs,
and diffs the desired state against an optionally-supplied current
network object.

Validation failures raise ``SystemExit(65)`` with a ``<file>: <key>
...`` message — the convention established by ``server_defaults.py``.
"""

from __future__ import annotations

import ipaddress
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

from .zones import KNOWN_ZONES, LOCATION_TO_ZONE


@dataclass(frozen=True, slots=True)
class NetworkSpec:
    """Desired top-level Hetzner network."""

    name: str
    ip_range: str
    network_zone: str


@dataclass(frozen=True, slots=True)
class SubnetSpec:
    """Desired subnet under a Hetzner network. Always ``type='cloud'``."""

    ip_range: str
    network_zone: str
    type: str = "cloud"


@dataclass(frozen=True, slots=True)
class DesiredState:
    """The full reconciliation target derived from ``.otsinfra.yaml``.

    ``subnets`` is sorted by numeric ``ip_range`` and deduplicated, so
    the action stream is stable across runs.
    """

    network: NetworkSpec
    subnets: tuple[SubnetSpec, ...]


@dataclass(frozen=True, slots=True)
class Action:
    """One reconciliation step.

    ``kind`` is one of:
      - ``"ok"`` — desired state matches current; no mutation needed.
      - ``"create-network"`` — top-level network is missing.
      - ``"create-subnet"`` — a desired subnet is missing.
      - ``"drift"`` — current state diverges from desired and the
        operator must reconcile manually.

    ``target`` is a short human-readable identifier (e.g.
    ``"network priv-net"`` or ``"subnet 10.101.0.0/24"``). ``message``
    fills in the rest of the line printed by ``ensure``.
    """

    kind: str
    target: str
    message: str


# ---------------------------------------------------------------------------
# parse_marker — validate .otsinfra.yaml and produce DesiredState
# ---------------------------------------------------------------------------


def _fail(marker_path: Path, msg: str) -> NoReturn:
    """Print ``<file>: <msg>`` to stderr and raise ``SystemExit(65)``.

    Numeric exit code is load-bearing — operators and CI pipelines branch
    on 65 (data error) vs 70 (drift) vs 1 (API error).
    """
    print(f"{marker_path}: {msg}", file=sys.stderr)
    raise SystemExit(65)


def _require_str(marker_path: Path, key: str, raw: object) -> str:
    if not isinstance(raw, str) or not raw:
        got = type(raw).__name__
        _fail(marker_path, f"network.{key} must be a non-empty str, got {got}: {raw!r}")
    return raw  # type: ignore[return-value]


def _parse_master_cidr(marker_path: Path, ip_range: str) -> ipaddress.IPv4Network:
    """Parse the master ``ip_range`` and enforce the /8–/16 size band."""
    try:
        net = ipaddress.ip_network(ip_range, strict=True)
    except (ValueError, TypeError) as exc:
        _fail(marker_path, f"network.ip_range is not a valid CIDR: {ip_range!r} ({exc})")
    if not isinstance(net, ipaddress.IPv4Network):
        _fail(marker_path, f"network.ip_range must be IPv4, got {ip_range!r}")
    if net.prefixlen > 16:
        _fail(
            marker_path,
            f"network.ip_range must be /8–/16 (got /{net.prefixlen} for {ip_range!r}); "
            f"smaller blocks cannot hold per-profile /24 subnets",
        )
    if net.prefixlen < 8:
        _fail(
            marker_path,
            f"network.ip_range must be /8–/16 (got /{net.prefixlen} for {ip_range!r})",
        )
    return net  # type: ignore[return-value]


def _derive_subnets(
    marker_path: Path,
    hosts: dict[str, Any],
    master: ipaddress.IPv4Network,
    network_zone: str,
) -> tuple[SubnetSpec, ...]:
    """Walk ``hosts.*.private_ip_address`` and build the deduplicated subnet set.

    Each host IP is rounded down to its enclosing ``/24``. Hosts without
    ``private_ip_address`` are skipped. Hosts whose IP is outside the
    master fail loud (exit 65).
    """
    seen_subnets: dict[str, SubnetSpec] = {}
    for role, host in hosts.items():
        if not isinstance(host, dict):
            # Foreign-tool sections (rots, caddy) come through here too;
            # skip without complaint.
            continue
        raw_ip = host.get("private_ip_address")
        if raw_ip is None:
            continue
        if not isinstance(raw_ip, str) or not raw_ip:
            _fail(
                marker_path,
                f"hosts.{role}.private_ip_address must be a non-empty str, "
                f"got {type(raw_ip).__name__}: {raw_ip!r}",
            )
        try:
            host_ip = ipaddress.ip_address(raw_ip)
        except (ValueError, TypeError) as exc:
            _fail(
                marker_path,
                f"hosts.{role}.private_ip_address is not a valid IP: {raw_ip!r} ({exc})",
            )
        if host_ip not in master:
            _fail(
                marker_path,
                f"hosts.{role}.private_ip_address {raw_ip} is outside "
                f"network.ip_range {master.with_prefixlen}",
            )
        # Compute the enclosing /24. _parse_master_cidr already forbids
        # masters smaller than /16, so the /24 is guaranteed to fit.
        slash24 = ipaddress.ip_network(f"{raw_ip}/24", strict=False)
        cidr = slash24.with_prefixlen
        if cidr not in seen_subnets:
            seen_subnets[cidr] = SubnetSpec(ip_range=cidr, network_zone=network_zone)

    # Sort by integer network address for stable, human-readable order.
    sorted_subnets = sorted(
        seen_subnets.values(),
        key=lambda s: int(ipaddress.ip_network(s.ip_range).network_address),
    )
    return tuple(sorted_subnets)


def _validate_locations(
    marker_path: Path,
    hosts: dict[str, Any],
    network_zone: str,
) -> None:
    """Each host's ``location`` must map to the network's ``network_zone``.

    Hosts without ``location`` are skipped — they may be defined for
    other tooling that doesn't care about Hetzner placement.
    """
    for role, host in hosts.items():
        if not isinstance(host, dict):
            continue
        location = host.get("location")
        if location is None:
            continue
        if not isinstance(location, str) or not location:
            _fail(
                marker_path,
                f"hosts.{role}.location must be a non-empty str, "
                f"got {type(location).__name__}: {location!r}",
            )
        zone = LOCATION_TO_ZONE.get(location)
        if zone is None:
            _fail(
                marker_path,
                f"hosts.{role}.location {location!r} is not a known Hetzner "
                f"location. Known: {sorted(LOCATION_TO_ZONE)}",
            )
        if zone != network_zone:
            _fail(
                marker_path,
                f"hosts.{role}.location {location!r} is in zone {zone!r} "
                f"but network.network_zone is {network_zone!r}",
            )


def parse_marker(marker: dict, *, marker_path: Path) -> DesiredState:
    """Validate the loaded marker dict and produce a ``DesiredState``.

    Required keys:
      - ``network.name`` — non-empty str
      - ``network.ip_range`` — IPv4 CIDR /8–/16
      - ``network.network_zone`` — value from :data:`KNOWN_ZONES`

    Per-host validation:
      - Each ``hosts.<role>.private_ip_address`` (when present) must lie
        inside the master CIDR.
      - Each ``hosts.<role>.location`` (when present) must map to the
        same zone as ``network.network_zone``.

    Hosts may omit either field; they're skipped for that check.
    """
    if not isinstance(marker, dict):
        _fail(marker_path, f"marker root must be a mapping, got {type(marker).__name__}")

    network_block = marker.get("network")
    if network_block is None:
        _fail(
            marker_path,
            "missing top-level 'network:' block. Expected keys: name, ip_range, network_zone.",
        )
    if not isinstance(network_block, dict):
        _fail(
            marker_path,
            f"'network' must be a mapping, got {type(network_block).__name__}",
        )

    name = _require_str(marker_path, "name", network_block.get("name"))
    ip_range = _require_str(marker_path, "ip_range", network_block.get("ip_range"))
    network_zone = _require_str(marker_path, "network_zone", network_block.get("network_zone"))

    if network_zone not in KNOWN_ZONES:
        _fail(
            marker_path,
            f"network.network_zone {network_zone!r} is not a known Hetzner zone. "
            f"Known: {sorted(KNOWN_ZONES)}",
        )

    master = _parse_master_cidr(marker_path, ip_range)

    hosts_raw = marker.get("hosts", {})
    if not isinstance(hosts_raw, dict):
        _fail(marker_path, f"'hosts' must be a mapping, got {type(hosts_raw).__name__}")

    _validate_locations(marker_path, hosts_raw, network_zone)
    subnets = _derive_subnets(marker_path, hosts_raw, master, network_zone)

    return DesiredState(
        network=NetworkSpec(name=name, ip_range=ip_range, network_zone=network_zone),
        subnets=subnets,
    )


# ---------------------------------------------------------------------------
# diff_state — compare desired against current Hetzner network
# ---------------------------------------------------------------------------


def diff_state(desired: DesiredState, current_network: Any) -> list[Action]:
    """Produce the ordered action list for ``ensure``.

    ``current_network`` is a hcloud ``Network`` object (with ``id``,
    ``ip_range``, ``subnets`` attributes) or ``None`` when the network
    does not yet exist.

    Logic:
      - If the network is missing, the first action is ``create-network``
        followed by one ``create-subnet`` per desired subnet (we know
        nothing exists yet).
      - If the network exists with a matching ``ip_range``, emit ``ok``
        for the network and per-subnet actions based on the existing
        subnet list.
      - If the network exists but its ``ip_range`` differs, emit
        ``drift`` for the network and skip subnet evaluation (the
        operator has to reconcile the master before subnets matter).
    """
    actions: list[Action] = []

    if current_network is None:
        actions.append(
            Action(
                kind="create-network",
                target=f"network {desired.network.name}",
                message=f"ip_range={desired.network.ip_range}",
            )
        )
        for subnet in desired.subnets:
            actions.append(
                Action(
                    kind="create-subnet",
                    target=f"subnet {subnet.ip_range}",
                    message=f"zone={subnet.network_zone}",
                )
            )
        return actions

    # Network exists — compare master ip_range first.
    current_ip_range = getattr(current_network, "ip_range", None)
    network_id = getattr(current_network, "id", "?")
    if current_ip_range != desired.network.ip_range:
        actions.append(
            Action(
                kind="drift",
                target=f"network {desired.network.name}",
                message=(
                    f"ip_range mismatch: desired={desired.network.ip_range} "
                    f"current={current_ip_range} (id={network_id})"
                ),
            )
        )
        # Don't evaluate subnets when the master is wrong; surface the
        # one big problem and let the operator reconcile.
        return actions

    actions.append(
        Action(
            kind="ok",
            target=f"network {desired.network.name}",
            message=f"(id={network_id}) ip_range={current_ip_range}",
        )
    )

    # Index existing subnets by ip_range.
    existing_subnets = getattr(current_network, "subnets", None) or []
    by_range = {s.ip_range: s for s in existing_subnets}

    for subnet in desired.subnets:
        existing = by_range.get(subnet.ip_range)
        if existing is None:
            actions.append(
                Action(
                    kind="create-subnet",
                    target=f"subnet {subnet.ip_range}",
                    message=f"zone={subnet.network_zone}",
                )
            )
            continue
        existing_zone = getattr(existing, "network_zone", None)
        existing_type = getattr(existing, "type", None)
        if existing_zone != subnet.network_zone or existing_type != subnet.type:
            actions.append(
                Action(
                    kind="drift",
                    target=f"subnet {subnet.ip_range}",
                    message=(
                        f"zone/type mismatch: desired zone={subnet.network_zone} "
                        f"type={subnet.type}; current zone={existing_zone} "
                        f"type={existing_type}"
                    ),
                )
            )
        else:
            actions.append(
                Action(
                    kind="ok",
                    target=f"subnet {subnet.ip_range}",
                    message="(already exists)",
                )
            )

    return actions
