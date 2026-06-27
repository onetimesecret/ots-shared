# tests/upcloud/test_network_plan.py

"""Unit tests for ots_shared.upcloud.network_plan.

Pure-logic: no UpCloud client, no filesystem. ``parse_marker`` takes a
dict + path; ``diff_state`` takes a DesiredState + a (possibly None)
network-like object. The UpCloud model has ONE ip range per network, an
atomic zone (no network_zone), and validates host IPs against the range.
Validation failures surface as ``SystemExit(65)``.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ots_shared.upcloud.network_plan import (
    Action,
    DesiredState,
    NetworkSpec,
    diff_state,
    parse_marker,
)

MARKER_PATH = Path("/tmp/fake/otsinfra.yaml")


def _valid_network() -> dict:
    return {"name": "priv-net", "ip_range": "10.0.0.0/24", "zone": "de-fra1"}


def _marker(hosts: dict | None = None, network: dict | None = None) -> dict:
    m: dict = {"network": network if network is not None else _valid_network()}
    if hosts is not None:
        m["hosts"] = hosts
    return m


def _mock_network(ip_networks=None, uuid="net-1", **attrs):
    n = MagicMock()
    n.uuid = uuid
    n.ip_networks = ip_networks
    for k, v in attrs.items():
        setattr(n, k, v)
    return n


# ---------------------------------------------------------------------------
# parse_marker — happy path
# ---------------------------------------------------------------------------


class TestParseMarkerHappyPath:
    def test_minimal_valid(self):
        ds = parse_marker(_marker(), marker_path=MARKER_PATH)
        assert isinstance(ds, DesiredState)
        assert ds.network == NetworkSpec(name="priv-net", ip_range="10.0.0.0/24", zone="de-fra1")

    def test_single_ip_range_only(self):
        # No SubnetSpec / subnets attribute on DesiredState — one range.
        ds = parse_marker(_marker(), marker_path=MARKER_PATH)
        assert not hasattr(ds, "subnets")

    def test_host_ip_inside_range_ok(self):
        ds = parse_marker(
            _marker({"db": {"private_ip_address": "10.0.0.11"}}),
            marker_path=MARKER_PATH,
        )
        assert ds.network.ip_range == "10.0.0.0/24"

    def test_unknown_zone_warns_but_accepts(self, capsys):
        m = _marker(network={"name": "n", "ip_range": "10.0.0.0/24", "zone": "xx-new1"})
        ds = parse_marker(m, marker_path=MARKER_PATH)
        assert ds.network.zone == "xx-new1"
        assert "warning" in capsys.readouterr().err

    def test_ordinals_validated(self):
        m = _marker({"web": {"ordinals": {"02": {"private_ip_address": "10.0.0.22"}}}})
        ds = parse_marker(m, marker_path=MARKER_PATH)
        assert ds.network.name == "priv-net"


# ---------------------------------------------------------------------------
# parse_marker — validation failures (exit 65)
# ---------------------------------------------------------------------------


def _assert_fail(marker: dict, substring: str, capsys) -> None:
    """parse_marker must raise SystemExit(65) and print substring to stderr.

    The planner prints the diagnostic to stderr and raises a bare
    SystemExit(65) (the code is load-bearing for CI), mirroring the Hetzner
    planner's _fail convention.
    """
    with pytest.raises(SystemExit) as exc:
        parse_marker(marker, marker_path=MARKER_PATH)
    assert exc.value.code == 65
    assert substring in capsys.readouterr().err


class TestParseMarkerValidation:
    def test_missing_network_block(self, capsys):
        _assert_fail({}, "missing top-level 'network:'", capsys)

    def test_network_not_a_mapping(self, capsys):
        _assert_fail({"network": "x"}, "'network' must be a mapping", capsys)

    def test_network_zone_key_rejected(self, capsys):
        # Hetzner-shaped marker: network_zone has no meaning for UpCloud.
        m = _marker(
            network={
                "name": "n",
                "ip_range": "10.0.0.0/24",
                "zone": "de-fra1",
                "network_zone": "eu-central",
            }
        )
        _assert_fail(m, "network_zone is not valid", capsys)

    def test_missing_name(self, capsys):
        _assert_fail(
            _marker(network={"ip_range": "10.0.0.0/24", "zone": "de-fra1"}),
            "network.name",
            capsys,
        )

    def test_missing_zone(self, capsys):
        _assert_fail(
            _marker(network={"name": "n", "ip_range": "10.0.0.0/24"}), "network.zone", capsys
        )

    def test_invalid_cidr(self, capsys):
        _assert_fail(
            _marker(network={"name": "n", "ip_range": "not-a-cidr", "zone": "de-fra1"}),
            "not a valid CIDR",
            capsys,
        )

    def test_ipv6_range_rejected(self, capsys):
        _assert_fail(
            _marker(network={"name": "n", "ip_range": "fd00::/64", "zone": "de-fra1"}),
            "IPv4-only",
            capsys,
        )

    def test_range_too_large(self, capsys):
        _assert_fail(
            _marker(network={"name": "n", "ip_range": "0.0.0.0/4", "zone": "de-fra1"}),
            "/8",
            capsys,
        )

    def test_range_too_small(self, capsys):
        _assert_fail(
            _marker(network={"name": "n", "ip_range": "10.0.0.0/30", "zone": "de-fra1"}),
            "/29",
            capsys,
        )

    def test_host_ip_outside_range_fail_loud(self, capsys):
        _assert_fail(_marker({"db": {"private_ip_address": "10.1.0.5"}}), "outside", capsys)

    def test_host_ipv6_rejected(self, capsys):
        _assert_fail(_marker({"db": {"private_ip_address": "fd00::5"}}), "IPv4-only", capsys)

    def test_host_cidr_not_subset(self, capsys):
        _assert_fail(_marker({"db": {"private_ip_cidr": "10.2.0.0/28"}}), "not a subset", capsys)

    def test_ordinal_ip_outside_range(self, capsys):
        _assert_fail(
            _marker({"web": {"ordinals": {"02": {"private_ip_address": "192.168.1.1"}}}}),
            "outside",
            capsys,
        )


# ---------------------------------------------------------------------------
# diff_state
# ---------------------------------------------------------------------------


class TestDiffState:
    def _desired(self):
        return DesiredState(
            network=NetworkSpec(name="priv-net", ip_range="10.0.0.0/24", zone="de-fra1")
        )

    def test_missing_network_creates(self):
        actions = diff_state(self._desired(), None)
        assert len(actions) == 1
        assert actions[0].kind == "create-network"
        assert "10.0.0.0/24" in actions[0].message

    def test_matching_network_ok(self):
        net = _mock_network(ip_networks=[{"family": "IPv4", "address": "10.0.0.0/24"}])
        actions = diff_state(self._desired(), net)
        assert len(actions) == 1
        assert actions[0].kind == "ok"

    def test_drift_on_range_mismatch(self):
        net = _mock_network(ip_networks=[{"family": "IPv4", "address": "10.9.0.0/24"}])
        actions = diff_state(self._desired(), net)
        assert len(actions) == 1
        assert actions[0].kind == "drift"
        assert "10.9.0.0/24" in actions[0].message

    def test_flattened_ip_range_attr(self):
        # A simplified mock exposing ip_range directly still matches.
        net = MagicMock(uuid="net-1", ip_networks=None, ip_range="10.0.0.0/24")
        actions = diff_state(self._desired(), net)
        assert actions[0].kind == "ok"

    def test_ipv6_entry_ignored_for_match(self):
        net = _mock_network(
            ip_networks=[
                {"family": "IPv6", "address": "fd00::/64"},
                {"family": "IPv4", "address": "10.0.0.0/24"},
            ]
        )
        actions = diff_state(self._desired(), net)
        assert actions[0].kind == "ok"


def test_action_is_frozen():
    a = Action(kind="ok", target="t", message="m")
    with pytest.raises(Exception):
        a.kind = "drift"  # type: ignore[misc]
