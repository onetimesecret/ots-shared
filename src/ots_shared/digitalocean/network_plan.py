# src/ots_shared/digitalocean/network_plan.py

"""Pure logic for the DigitalOcean ``network`` (VPC) reconciler.

No I/O — accepts an already-loaded ``otsinfra.yaml`` dict, validates the schema,
and diffs the desired state against an optionally-supplied current VPC object.

The DigitalOcean model is the flattest of the three providers:

  * **One IP range per VPC, no subnets, no routes.** ``vpcs.create`` sends a
    single ``ip_range`` for a region-scoped VPC; there is no ``create_subnet`` /
    ``add_route`` (multi-provider.md §5.2). So :class:`DesiredState` carries
    exactly one :class:`NetworkSpec` and there is no ``SubnetSpec``.
  * **Atomic region.** ``region == zone``; there is no ``network_zone``
    grouping. The VPC's ``region`` is the same atomic slug used by droplets — a
    ``network_zone`` key is fail-loud to catch a Hetzner-shaped marker.
  * **No pinned private IPs.** DO assigns droplet private IPs sequentially, so a
    host-level ``private_ip_*`` field is fail-loud (§5.4a) rather than validated
    against the range.

Validation failures raise ``SystemExit(65)`` with a ``<file>: <key> ...``
message — the convention shared with the Hetzner / UpCloud planners.
"""

from __future__ import annotations

import ipaddress
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

from .droplet_defaults import _PINNED_IP_KEYS
from .regions import KNOWN_REGIONS


@dataclass(frozen=True, slots=True)
class NetworkSpec:
    """Desired DigitalOcean VPC — exactly one IPv4 range, region-scoped."""

    name: str
    ip_range: str
    region: str
    family: str = "IPv4"


@dataclass(frozen=True, slots=True)
class DesiredState:
    """The full reconciliation target derived from ``otsinfra.yaml``.

    DO collapses network and subnet 1:1, so there is a single
    :class:`NetworkSpec` and no subnet list.
    """

    network: NetworkSpec


@dataclass(frozen=True, slots=True)
class Action:
    """One reconciliation step.

    ``kind`` is one of:
      - ``"ok"`` — desired matches current; no mutation needed.
      - ``"create-network"`` — the VPC is missing.
      - ``"drift"`` — current diverges from desired; operator reconciles.
    """

    kind: str
    target: str
    message: str


def _fail(marker_path: Path, msg: str) -> NoReturn:
    """Print ``<file>: <msg>`` to stderr and raise ``SystemExit(65)``."""
    print(f"{marker_path}: {msg}", file=sys.stderr)
    raise SystemExit(65)


def _require_str(marker_path: Path, key: str, raw: object) -> str:
    if not isinstance(raw, str) or not raw:
        got = type(raw).__name__
        _fail(marker_path, f"network.{key} must be a non-empty str, got {got}: {raw!r}")
    return raw  # type: ignore[return-value]


def _parse_master_cidr(marker_path: Path, ip_range: str) -> ipaddress.IPv4Network:
    """Parse the VPC ``ip_range`` and enforce an IPv4 /8–/28 band.

    DigitalOcean VPC ranges must be private (RFC1918) IPv4 between /16 and /28 in
    the API; we accept a slightly wider /8–/28 band offline and let the server be
    authoritative on the exact bounds. An IPv6 range fails loud (VPC is IPv4).
    """
    try:
        net = ipaddress.ip_network(ip_range, strict=True)
    except (ValueError, TypeError) as exc:
        _fail(marker_path, f"network.ip_range is not a valid CIDR: {ip_range!r} ({exc})")
    if not isinstance(net, ipaddress.IPv4Network):
        _fail(
            marker_path,
            f"network.ip_range must be IPv4 (DigitalOcean VPC is IPv4-only), got {ip_range!r}",
        )
    if net.prefixlen < 8:
        _fail(
            marker_path,
            f"network.ip_range must be /8–/28 (got /{net.prefixlen} for {ip_range!r})",
        )
    if net.prefixlen > 28:
        _fail(
            marker_path,
            f"network.ip_range must be /8–/28 (got /{net.prefixlen} for {ip_range!r}); "
            f"too small to host droplets",
        )
    return net  # type: ignore[return-value]


def _reject_host_pinned_ips(marker_path: Path, hosts: dict[str, Any]) -> None:
    """Fail loud if any host declares a ``private_ip_*`` field (§5.4a).

    DO assigns private IPs automatically, so a pinned address is unsatisfiable.
    This mirrors the host-defaults rejection so the gap is caught from the
    network-reconciler entry point too.
    """
    for role, host in hosts.items():
        if not isinstance(host, dict):
            continue  # foreign-tool section
        present = [k for k in _PINNED_IP_KEYS if k in host]
        if present:
            _fail(
                marker_path,
                f"hosts.{role}.{present[0]} is set but the digitalocean provider "
                f"assigns private IPs automatically and cannot honor a pinned "
                f"address; remove private_ip_* from otsinfra.yaml.",
            )


def parse_marker(marker: dict, *, marker_path: Path) -> DesiredState:
    """Validate the loaded marker dict and produce a ``DesiredState``.

    Required keys under the top-level ``network:`` block:
      - ``name`` — non-empty str
      - ``ip_range`` — IPv4 CIDR /8–/28
      - ``region`` — the atomic region slug (e.g. ``nyc3``). There is **no**
        ``network_zone`` for DO; a ``network_zone`` key is fail-loud to catch a
        Hetzner-shaped marker.

    Host-level ``private_ip_*`` fields are fail-loud (§5.4a). The region is
    validated against :data:`KNOWN_REGIONS` only as a warning-grade hint — an
    unknown-but-well-formed slug is accepted (the catalog grows; the server-side
    call is authoritative).
    """
    if not isinstance(marker, dict):
        _fail(marker_path, f"marker root must be a mapping, got {type(marker).__name__}")

    network_block = marker.get("network")
    if network_block is None:
        _fail(
            marker_path,
            "missing top-level 'network:' block. Expected keys: name, ip_range, region.",
        )
    if not isinstance(network_block, dict):
        _fail(marker_path, f"'network' must be a mapping, got {type(network_block).__name__}")

    if "network_zone" in network_block:
        _fail(
            marker_path,
            "network.network_zone is not valid for DigitalOcean (regions are atomic; "
            "use network.region with an atomic region slug like nyc3).",
        )

    name = _require_str(marker_path, "name", network_block.get("name"))
    ip_range = _require_str(marker_path, "ip_range", network_block.get("ip_range"))
    region = _require_str(marker_path, "region", network_block.get("region"))

    if region not in KNOWN_REGIONS:
        # Non-fatal: print a hint but accept it (catalog grows over time).
        print(
            f"{marker_path}: warning: network.region {region!r} is not in the known "
            f"DigitalOcean region set {sorted(KNOWN_REGIONS)}; proceeding (server-side "
            f"validation is authoritative).",
            file=sys.stderr,
        )

    _parse_master_cidr(marker_path, ip_range)

    hosts_raw = marker.get("hosts", {})
    if not isinstance(hosts_raw, dict):
        _fail(marker_path, f"'hosts' must be a mapping, got {type(hosts_raw).__name__}")

    _reject_host_pinned_ips(marker_path, hosts_raw)

    return DesiredState(network=NetworkSpec(name=name, ip_range=ip_range, region=region))


def _current_ip_range(current_network: Any) -> str | None:
    """Extract the IPv4 range from a current DO VPC dict or object.

    A pydo VPC is a dict with an ``ip_range`` key; falls back to an attribute so
    a simplified mock works in tests. Returns the range string, or ``None``.
    """
    if isinstance(current_network, dict):
        return current_network.get("ip_range")
    return getattr(current_network, "ip_range", None)


def _current_id(current_network: Any) -> str:
    """Best-effort id of a current VPC (dict ``id`` or attribute), else ``?``."""
    if isinstance(current_network, dict):
        return str(current_network.get("id", "?"))
    return str(getattr(current_network, "id", "?"))


def diff_state(desired: DesiredState, current_network: Any) -> list[Action]:
    """Produce the ordered action list for the VPC reconcile.

    ``current_network`` is a DO VPC dict (with ``id`` / ``ip_range``) or ``None``
    when the VPC does not yet exist.

      - Missing VPC → a single ``create-network`` action.
      - Existing VPC with a matching range → ``ok``.
      - Existing VPC whose range differs → ``drift`` (the operator must
        reconcile; DO cannot resize an in-use VPC range in place).
    """
    if current_network is None:
        return [
            Action(
                kind="create-network",
                target=f"vpc {desired.network.name}",
                message=f"ip_range={desired.network.ip_range} region={desired.network.region}",
            )
        ]

    network_id = _current_id(current_network)
    current_range = _current_ip_range(current_network)
    if current_range != desired.network.ip_range:
        return [
            Action(
                kind="drift",
                target=f"vpc {desired.network.name}",
                message=(
                    f"ip_range mismatch: desired={desired.network.ip_range} "
                    f"current={current_range} (id={network_id})"
                ),
            )
        ]

    return [
        Action(
            kind="ok",
            target=f"vpc {desired.network.name}",
            message=f"(id={network_id}) ip_range={current_range}",
        )
    ]
