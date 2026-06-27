# src/ots_shared/upcloud/network_plan.py

"""Pure logic for the UpCloud ``network ensure`` reconciler.

No I/O — accepts an already-loaded ``otsinfra.yaml`` dict, validates the
schema, and diffs the desired state against an optionally-supplied current
network object.

The UpCloud model differs from Hetzner in two load-bearing ways:

  * **One IP range per network.** ``create_network`` sends a *single*
    ``ip_network`` dict, not a list. There are no sub-subnets — the network
    *is* the range. So :class:`DesiredState` carries exactly one
    :class:`NetworkSpec` and there is no ``SubnetSpec``.
  * **Atomic zone.** ``region == zone``; there is no ``network_zone``
    grouping. The network's ``zone`` is the same atomic id used by servers.

Every per-host private IP must fall inside the network range. Private SDN
networks are IPv4-only. Validation failures raise ``SystemExit(65)`` with a
``<file>: <key> ...`` message — the convention from the Hetzner planner.
"""

from __future__ import annotations

import ipaddress
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

from .zones import KNOWN_ZONES


@dataclass(frozen=True, slots=True)
class NetworkSpec:
    """Desired UpCloud private network — exactly one IPv4 range, zone-scoped."""

    name: str
    ip_range: str
    zone: str
    family: str = "IPv4"


@dataclass(frozen=True, slots=True)
class DesiredState:
    """The full reconciliation target derived from ``otsinfra.yaml``.

    UpCloud collapses network and subnet 1:1, so there is a single
    :class:`NetworkSpec` and no subnet list.
    """

    network: NetworkSpec


@dataclass(frozen=True, slots=True)
class Action:
    """One reconciliation step.

    ``kind`` is one of:
      - ``"ok"`` — desired matches current; no mutation needed.
      - ``"create-network"`` — the network is missing.
      - ``"drift"`` — current diverges from desired; operator reconciles.
    """

    kind: str
    target: str
    message: str


# ---------------------------------------------------------------------------
# parse_marker — validate otsinfra.yaml and produce DesiredState
# ---------------------------------------------------------------------------


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
    """Parse the network ``ip_range`` and enforce an IPv4 /8–/29 band.

    The lower bound (/8) guards against absurdly large ranges; the upper
    bound (/29, 8 addresses) ensures at least a few usable hosts. UpCloud
    private SDN is IPv4-only, so an IPv6 range fails loud.
    """
    try:
        net = ipaddress.ip_network(ip_range, strict=True)
    except (ValueError, TypeError) as exc:
        _fail(marker_path, f"network.ip_range is not a valid CIDR: {ip_range!r} ({exc})")
    if not isinstance(net, ipaddress.IPv4Network):
        _fail(
            marker_path,
            f"network.ip_range must be IPv4 (UpCloud private SDN is IPv4-only), got {ip_range!r}",
        )
    if net.prefixlen < 8:
        _fail(
            marker_path,
            f"network.ip_range must be /8–/29 (got /{net.prefixlen} for {ip_range!r})",
        )
    if net.prefixlen > 29:
        _fail(
            marker_path,
            f"network.ip_range must be /8–/29 (got /{net.prefixlen} for {ip_range!r}); "
            f"too small to host servers",
        )
    return net  # type: ignore[return-value]


def _validate_host_ips(
    marker_path: Path,
    hosts: dict[str, Any],
    master: ipaddress.IPv4Network,
) -> None:
    """Every declared private IP / CIDR must fall inside the network range.

    Validates ``hosts.<role>.private_ip_address``,
    ``hosts.<role>.private_ip_cidr``, and per-replica
    ``ordinals.<NN>.private_ip_address``. UpCloud has no sub-subnets, so a
    private_ip_cidr is checked as a subset of the single network range.
    Hosts with none of these are skipped. Anything outside the range, or any
    IPv6 value, fails loud (exit 65).
    """

    def check_ip(role: str, raw_ip: object, source: str) -> None:
        if raw_ip is None:
            return
        if not isinstance(raw_ip, str) or not raw_ip:
            _fail(
                marker_path,
                f"hosts.{role}.{source} must be a non-empty str, "
                f"got {type(raw_ip).__name__}: {raw_ip!r}",
            )
        try:
            host_ip = ipaddress.ip_address(raw_ip)
        except (ValueError, TypeError) as exc:
            _fail(marker_path, f"hosts.{role}.{source} is not a valid IP: {raw_ip!r} ({exc})")
        if not isinstance(host_ip, ipaddress.IPv4Address):
            _fail(
                marker_path,
                f"hosts.{role}.{source} must be IPv4 (UpCloud private SDN is "
                f"IPv4-only), got {raw_ip!r}",
            )
        if host_ip not in master:
            _fail(
                marker_path,
                f"hosts.{role}.{source} {raw_ip} is outside "
                f"network.ip_range {master.with_prefixlen}",
            )

    for role, host in hosts.items():
        if not isinstance(host, dict):
            continue  # foreign-tool section

        check_ip(role, host.get("private_ip_address"), "private_ip_address")

        raw_cidr = host.get("private_ip_cidr")
        if raw_cidr is not None:
            if not isinstance(raw_cidr, str) or not raw_cidr:
                _fail(
                    marker_path,
                    f"hosts.{role}.private_ip_cidr must be a non-empty str, "
                    f"got {type(raw_cidr).__name__}: {raw_cidr!r}",
                )
            try:
                host_cidr = ipaddress.ip_network(raw_cidr, strict=True)
            except (ValueError, TypeError) as exc:
                _fail(
                    marker_path,
                    f"hosts.{role}.private_ip_cidr is not a valid CIDR: {raw_cidr!r} ({exc})",
                )
            if not isinstance(host_cidr, ipaddress.IPv4Network):
                _fail(
                    marker_path,
                    f"hosts.{role}.private_ip_cidr must be IPv4, got {raw_cidr!r}",
                )
            if not host_cidr.subnet_of(master):
                _fail(
                    marker_path,
                    f"hosts.{role}.private_ip_cidr {raw_cidr} is not a subset of "
                    f"network.ip_range {master.with_prefixlen}",
                )

        ordinals = host.get("ordinals")
        if isinstance(ordinals, dict):
            for ord_key, per_ord in ordinals.items():
                if not isinstance(per_ord, dict):
                    continue
                check_ip(
                    role,
                    per_ord.get("private_ip_address"),
                    f"ordinals.{ord_key}.private_ip_address",
                )


def parse_marker(marker: dict, *, marker_path: Path) -> DesiredState:
    """Validate the loaded marker dict and produce a ``DesiredState``.

    Required keys under the top-level ``network:`` block:
      - ``name`` — non-empty str
      - ``ip_range`` — IPv4 CIDR /8–/29
      - ``zone`` — the atomic zone id (e.g. ``de-fra1``). There is **no**
        ``network_zone`` for UpCloud; a ``network_zone`` key is fail-loud to
        catch a Hetzner-shaped marker.

    Per-host validation: every declared private IP / CIDR must lie inside
    ``network.ip_range``. The zone is validated against :data:`KNOWN_ZONES`
    only as a warning-grade hint — an unknown-but-well-formed id is accepted
    (the catalog grows; the server-side call is authoritative).
    """
    if not isinstance(marker, dict):
        _fail(marker_path, f"marker root must be a mapping, got {type(marker).__name__}")

    network_block = marker.get("network")
    if network_block is None:
        _fail(
            marker_path,
            "missing top-level 'network:' block. Expected keys: name, ip_range, zone.",
        )
    if not isinstance(network_block, dict):
        _fail(marker_path, f"'network' must be a mapping, got {type(network_block).__name__}")

    if "network_zone" in network_block:
        _fail(
            marker_path,
            "network.network_zone is not valid for UpCloud (zones are atomic; "
            "use network.zone with an atomic zone id like de-fra1).",
        )

    name = _require_str(marker_path, "name", network_block.get("name"))
    ip_range = _require_str(marker_path, "ip_range", network_block.get("ip_range"))
    zone = _require_str(marker_path, "zone", network_block.get("zone"))

    if zone not in KNOWN_ZONES:
        # Non-fatal: print a hint but accept it (catalog grows over time).
        print(
            f"{marker_path}: warning: network.zone {zone!r} is not in the known "
            f"UpCloud zone set {sorted(KNOWN_ZONES)}; proceeding (server-side "
            f"validation is authoritative).",
            file=sys.stderr,
        )

    master = _parse_master_cidr(marker_path, ip_range)

    hosts_raw = marker.get("hosts", {})
    if not isinstance(hosts_raw, dict):
        _fail(marker_path, f"'hosts' must be a mapping, got {type(hosts_raw).__name__}")

    _validate_host_ips(marker_path, hosts_raw, master)

    return DesiredState(network=NetworkSpec(name=name, ip_range=ip_range, zone=zone))


# ---------------------------------------------------------------------------
# diff_state — compare desired against the current UpCloud network
# ---------------------------------------------------------------------------


def _current_ip_range(current_network: Any) -> str | None:
    """Extract the single IPv4 range from a current UpCloud Network object.

    UpCloud's ``Network`` carries ``ip_networks`` — a list of dicts with an
    ``address`` key. Falls back to a plain ``ip_range``/``address``
    attribute so a simplified mock works in tests. Returns the first IPv4
    address found, or ``None``.
    """
    ip_networks = getattr(current_network, "ip_networks", None)
    if ip_networks:
        for entry in ip_networks:
            if isinstance(entry, dict):
                family = entry.get("family", "IPv4")
                if family == "IPv4" and entry.get("address"):
                    return entry["address"]
            else:
                addr = getattr(entry, "address", None)
                if addr:
                    return addr
    for attr in ("ip_range", "address"):
        val = getattr(current_network, attr, None)
        if val:
            return val
    return None


def diff_state(desired: DesiredState, current_network: Any) -> list[Action]:
    """Produce the ordered action list for ``ensure``.

    ``current_network`` is an UpCloud ``Network``-like object (with
    ``uuid``/``ip_networks`` or a flattened ``ip_range``) or ``None`` when
    the network does not yet exist.

      - Missing network → a single ``create-network`` action.
      - Existing network with a matching range → ``ok``.
      - Existing network whose range differs → ``drift`` (the operator must
        reconcile; UpCloud cannot resize an in-use network in place).
    """
    if current_network is None:
        return [
            Action(
                kind="create-network",
                target=f"network {desired.network.name}",
                message=f"ip_range={desired.network.ip_range} zone={desired.network.zone}",
            )
        ]

    network_id = getattr(current_network, "uuid", None) or getattr(current_network, "id", "?")
    current_range = _current_ip_range(current_network)
    if current_range != desired.network.ip_range:
        return [
            Action(
                kind="drift",
                target=f"network {desired.network.name}",
                message=(
                    f"ip_range mismatch: desired={desired.network.ip_range} "
                    f"current={current_range} (uuid={network_id})"
                ),
            )
        ]

    return [
        Action(
            kind="ok",
            target=f"network {desired.network.name}",
            message=f"(uuid={network_id}) ip_range={current_range}",
        )
    ]
