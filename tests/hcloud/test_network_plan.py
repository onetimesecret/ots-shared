# tests/hcloud/test_network_plan.py

"""Unit tests for ots_shared.hcloud.network_plan.

Pure-logic tests: no Hetzner client, no filesystem walk-up. ``parse_marker``
takes a dict + path; ``diff_state`` takes a DesiredState + a (possibly None)
network-like object whose attribute shape mirrors hcloud's ``Network``.

Validation failures must surface as ``SystemExit(65)`` per the spec; drift
detection on existing state surfaces as ``Action(kind="drift", ...)``
(the exit-70 mapping is the command's job, not the planner's).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ots_shared.hcloud.network_plan import (
    Action,
    DesiredState,
    NetworkSpec,
    SubnetSpec,
    diff_state,
    parse_marker,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


MARKER_PATH = Path("/tmp/fake/otsinfra.yaml")


def _valid_network() -> dict:
    return {
        "name": "priv-net",
        "ip_range": "10.101.0.0/16",
        "network_zone": "eu-central",
    }


def _marker_with_hosts(hosts: dict) -> dict:
    return {
        "environment": "eu2",
        "network": _valid_network(),
        "hosts": hosts,
    }


def _mock_subnet(ip_range: str, network_zone: str = "eu-central", type_: str = "cloud"):
    """Build a minimal subnet object with the attributes diff_state reads."""
    s = MagicMock()
    s.ip_range = ip_range
    s.network_zone = network_zone
    s.type = type_
    return s


def _mock_network(
    name: str = "priv-net",
    ip_range: str = "10.101.0.0/16",
    subnets: tuple = (),
):
    n = MagicMock()
    n.name = name
    n.ip_range = ip_range
    n.subnets = list(subnets)
    return n


# ---------------------------------------------------------------------------
# parse_marker — happy paths
# ---------------------------------------------------------------------------


class TestParseMarkerHappyPath:
    def test_three_hosts_three_subnets_sorted(self):
        marker = _marker_with_hosts(
            {
                "jumphost": {"private_ip_address": "10.101.0.5", "location": "nbg1"},
                "web": {"private_ip_address": "10.101.1.1", "location": "nbg1"},
                "db": {"private_ip_address": "10.101.2.1", "location": "nbg1"},
            }
        )
        state = parse_marker(marker, marker_path=MARKER_PATH)

        assert isinstance(state, DesiredState)
        assert state.network == NetworkSpec(
            name="priv-net",
            ip_range="10.101.0.0/16",
            network_zone="eu-central",
        )
        assert len(state.subnets) == 3
        # Sorted ascending by ip_range
        assert state.subnets[0].ip_range == "10.101.0.0/24"
        assert state.subnets[1].ip_range == "10.101.1.0/24"
        assert state.subnets[2].ip_range == "10.101.2.0/24"
        # Each subnet inherits the network's zone, defaults to type=cloud
        for sub in state.subnets:
            assert sub.network_zone == "eu-central"
            assert sub.type == "cloud"

    def test_two_hosts_in_same_24_dedup_to_one_subnet(self):
        marker = _marker_with_hosts(
            {
                "web1": {"private_ip_address": "10.101.1.1", "location": "nbg1"},
                "web2": {"private_ip_address": "10.101.1.2", "location": "nbg1"},
            }
        )
        state = parse_marker(marker, marker_path=MARKER_PATH)

        assert len(state.subnets) == 1
        assert state.subnets[0].ip_range == "10.101.1.0/24"

    def test_subnets_are_a_tuple(self):
        marker = _marker_with_hosts(
            {"web": {"private_ip_address": "10.101.1.1", "location": "nbg1"}}
        )
        state = parse_marker(marker, marker_path=MARKER_PATH)
        assert isinstance(state.subnets, tuple)


# ---------------------------------------------------------------------------
# parse_marker — schema failures (exit 65)
# ---------------------------------------------------------------------------


class TestParseMarkerSchemaFailures:
    def test_missing_network_block(self):
        marker = {"environment": "eu2", "hosts": {}}
        with pytest.raises(SystemExit) as exc:
            parse_marker(marker, marker_path=MARKER_PATH)
        assert exc.value.code == 65

    def test_missing_network_name(self):
        marker = {
            "network": {"ip_range": "10.101.0.0/16", "network_zone": "eu-central"},
            "hosts": {},
        }
        with pytest.raises(SystemExit) as exc:
            parse_marker(marker, marker_path=MARKER_PATH)
        assert exc.value.code == 65

    def test_missing_network_ip_range(self):
        marker = {
            "network": {"name": "priv-net", "network_zone": "eu-central"},
            "hosts": {},
        }
        with pytest.raises(SystemExit) as exc:
            parse_marker(marker, marker_path=MARKER_PATH)
        assert exc.value.code == 65

    def test_missing_network_zone(self):
        marker = {
            "network": {"name": "priv-net", "ip_range": "10.101.0.0/16"},
            "hosts": {},
        }
        with pytest.raises(SystemExit) as exc:
            parse_marker(marker, marker_path=MARKER_PATH)
        assert exc.value.code == 65

    def test_invalid_cidr(self):
        marker = {
            "network": {
                "name": "priv-net",
                "ip_range": "not-a-cidr",
                "network_zone": "eu-central",
            },
            "hosts": {},
        }
        with pytest.raises(SystemExit) as exc:
            parse_marker(marker, marker_path=MARKER_PATH)
        assert exc.value.code == 65

    def test_ip_range_prefix_too_small(self):
        # /24 is smaller than the allowed /8..16 range — must be rejected.
        marker = {
            "network": {
                "name": "priv-net",
                "ip_range": "10.101.0.0/24",
                "network_zone": "eu-central",
            },
            "hosts": {},
        }
        with pytest.raises(SystemExit) as exc:
            parse_marker(marker, marker_path=MARKER_PATH)
        assert exc.value.code == 65

    def test_unknown_network_zone(self):
        marker = {
            "network": {
                "name": "priv-net",
                "ip_range": "10.101.0.0/16",
                "network_zone": "mars",
            },
            "hosts": {},
        }
        with pytest.raises(SystemExit) as exc:
            parse_marker(marker, marker_path=MARKER_PATH)
        assert exc.value.code == 65

    def test_marker_root_must_be_dict(self):
        with pytest.raises(SystemExit) as exc:
            parse_marker(["not", "a", "dict"], marker_path=MARKER_PATH)  # type: ignore[arg-type]
        assert exc.value.code == 65

    def test_network_block_must_be_dict(self):
        marker = {"environment": "eu2", "network": "not-a-dict", "hosts": {}}
        with pytest.raises(SystemExit) as exc:
            parse_marker(marker, marker_path=MARKER_PATH)
        assert exc.value.code == 65

    def test_hosts_block_must_be_dict(self):
        marker = {"environment": "eu2", "network": _valid_network(), "hosts": "oops"}
        with pytest.raises(SystemExit) as exc:
            parse_marker(marker, marker_path=MARKER_PATH)
        assert exc.value.code == 65


# ---------------------------------------------------------------------------
# parse_marker — host validation failures (exit 65)
# ---------------------------------------------------------------------------


class TestParseMarkerHostFailures:
    def test_host_ip_outside_master(self):
        # master 10.101.0.0/16 — host 10.102.0.5 is outside.
        marker = _marker_with_hosts(
            {"stray": {"private_ip_address": "10.102.0.5", "location": "nbg1"}}
        )
        with pytest.raises(SystemExit) as exc:
            parse_marker(marker, marker_path=MARKER_PATH)
        assert exc.value.code == 65

    def test_host_location_does_not_match_zone(self):
        # zone=eu-central but location=ash maps to us-east.
        marker = _marker_with_hosts(
            {"web": {"private_ip_address": "10.101.1.1", "location": "ash"}}
        )
        with pytest.raises(SystemExit) as exc:
            parse_marker(marker, marker_path=MARKER_PATH)
        assert exc.value.code == 65

    def test_host_unknown_location(self):
        marker = _marker_with_hosts(
            {"web": {"private_ip_address": "10.101.1.1", "location": "atlantis"}}
        )
        with pytest.raises(SystemExit) as exc:
            parse_marker(marker, marker_path=MARKER_PATH)
        assert exc.value.code == 65

    def test_host_invalid_private_ip_string(self):
        marker = _marker_with_hosts(
            {"web": {"private_ip_address": "not-an-ip", "location": "nbg1"}}
        )
        with pytest.raises(SystemExit) as exc:
            parse_marker(marker, marker_path=MARKER_PATH)
        assert exc.value.code == 65

    def test_host_private_ip_wrong_type(self):
        # YAML ints under private_ip_address — fail loud.
        marker = _marker_with_hosts({"web": {"private_ip_address": 12345, "location": "nbg1"}})
        with pytest.raises(SystemExit) as exc:
            parse_marker(marker, marker_path=MARKER_PATH)
        assert exc.value.code == 65


# ---------------------------------------------------------------------------
# parse_marker — hosts that should be skipped, not errored
# ---------------------------------------------------------------------------


class TestParseMarkerHostsSkipped:
    def test_host_without_private_ip_is_skipped(self):
        marker = _marker_with_hosts(
            {
                "lonely": {"location": "nbg1"},  # no private_ip_address
                "web": {"private_ip_address": "10.101.1.1", "location": "nbg1"},
            }
        )
        state = parse_marker(marker, marker_path=MARKER_PATH)
        # Only 'web' contributes a subnet.
        assert len(state.subnets) == 1
        assert state.subnets[0].ip_range == "10.101.1.0/24"

    def test_host_without_location_is_not_zone_checked(self):
        # A host with private_ip_address but no location is allowed —
        # the zone-mismatch check is skipped (not failed).
        marker = _marker_with_hosts(
            {"web": {"private_ip_address": "10.101.1.1"}}  # no location
        )
        state = parse_marker(marker, marker_path=MARKER_PATH)
        assert len(state.subnets) == 1
        assert state.subnets[0].ip_range == "10.101.1.0/24"

    def test_no_hosts_block_yields_no_subnets(self):
        marker = {"environment": "eu2", "network": _valid_network()}
        state = parse_marker(marker, marker_path=MARKER_PATH)
        assert state.subnets == ()

    def test_non_dict_host_entry_skipped(self):
        # Foreign-tool sections may not even be dict-shaped at the host
        # level. The planner ignores them rather than failing.
        marker = _marker_with_hosts(
            {
                "web": {"private_ip_address": "10.101.1.1", "location": "nbg1"},
                "_meta": "this is not a host dict",
            }
        )
        state = parse_marker(marker, marker_path=MARKER_PATH)
        assert len(state.subnets) == 1


# ---------------------------------------------------------------------------
# parse_marker — private_ip_cidr and ordinals subnet derivation
# ---------------------------------------------------------------------------


class TestParseMarkerCidrAndOrdinals:
    """CIDR-only hosts and per-ordinal IPs both register subnets."""

    def test_cidr_only_host_registers_subnet(self):
        marker = _marker_with_hosts(
            {"web": {"private_ip_cidr": "10.101.3.0/24", "location": "nbg1"}}
        )
        state = parse_marker(marker, marker_path=MARKER_PATH)
        assert len(state.subnets) == 1
        assert state.subnets[0].ip_range == "10.101.3.0/24"

    def test_cidr_smaller_than_24_rolls_up_to_enclosing_24(self):
        marker = _marker_with_hosts(
            {"web": {"private_ip_cidr": "10.101.4.0/28", "location": "nbg1"}}
        )
        state = parse_marker(marker, marker_path=MARKER_PATH)
        assert len(state.subnets) == 1
        assert state.subnets[0].ip_range == "10.101.4.0/24"

    def test_legacy_ip_plus_cidr_dedup_when_in_same_24(self):
        marker = _marker_with_hosts(
            {
                "web": {
                    "private_ip_address": "10.101.5.11",
                    "private_ip_cidr": "10.101.5.0/24",
                    "location": "nbg1",
                }
            }
        )
        state = parse_marker(marker, marker_path=MARKER_PATH)
        assert len(state.subnets) == 1
        assert state.subnets[0].ip_range == "10.101.5.0/24"

    def test_ordinals_in_different_24s_produce_two_subnets(self):
        marker = _marker_with_hosts(
            {
                "web": {
                    "private_ip_cidr": "10.101.1.0/24",
                    "location": "nbg1",
                    "ordinals": {
                        "01": {"private_ip_address": "10.101.1.11"},
                        "02": {"private_ip_address": "10.101.7.99"},
                    },
                }
            }
        )
        state = parse_marker(marker, marker_path=MARKER_PATH)
        ranges = [s.ip_range for s in state.subnets]
        assert ranges == ["10.101.1.0/24", "10.101.7.0/24"]

    def test_cidr_outside_master_fails_loud(self, capsys):
        marker = _marker_with_hosts(
            {"web": {"private_ip_cidr": "10.200.0.0/24", "location": "nbg1"}}
        )
        with pytest.raises(SystemExit) as exc:
            parse_marker(marker, marker_path=MARKER_PATH)
        assert exc.value.code == 65
        assert "private_ip_cidr" in capsys.readouterr().err

    def test_cidr_invalid_string_fails_loud(self):
        marker = _marker_with_hosts({"web": {"private_ip_cidr": "not-a-cidr", "location": "nbg1"}})
        with pytest.raises(SystemExit) as exc:
            parse_marker(marker, marker_path=MARKER_PATH)
        assert exc.value.code == 65

    def test_cidr_wrong_type_fails_loud(self):
        marker = _marker_with_hosts({"web": {"private_ip_cidr": 24, "location": "nbg1"}})
        with pytest.raises(SystemExit) as exc:
            parse_marker(marker, marker_path=MARKER_PATH)
        assert exc.value.code == 65

    def test_ordinal_ip_outside_master_fails_loud(self):
        marker = _marker_with_hosts(
            {
                "web": {
                    "private_ip_cidr": "10.101.1.0/24",
                    "location": "nbg1",
                    "ordinals": {"02": {"private_ip_address": "10.200.5.5"}},
                }
            }
        )
        with pytest.raises(SystemExit) as exc:
            parse_marker(marker, marker_path=MARKER_PATH)
        assert exc.value.code == 65

    def test_non_dict_ordinal_value_skipped(self):
        # Defensive: a malformed ordinal entry should not crash the parser.
        marker = _marker_with_hosts(
            {
                "web": {
                    "private_ip_cidr": "10.101.1.0/24",
                    "location": "nbg1",
                    "ordinals": {"02": "should be a mapping"},
                }
            }
        )
        state = parse_marker(marker, marker_path=MARKER_PATH)
        assert len(state.subnets) == 1
        assert state.subnets[0].ip_range == "10.101.1.0/24"


# ---------------------------------------------------------------------------
# diff_state
# ---------------------------------------------------------------------------


def _desired(*subnets: SubnetSpec) -> DesiredState:
    return DesiredState(
        network=NetworkSpec(
            name="priv-net",
            ip_range="10.101.0.0/16",
            network_zone="eu-central",
        ),
        subnets=tuple(subnets),
    )


def _sub(ip_range: str, zone: str = "eu-central") -> SubnetSpec:
    return SubnetSpec(ip_range=ip_range, network_zone=zone)


class TestDiffStateNetworkMissing:
    def test_network_missing_creates_network_then_subnets(self):
        desired = _desired(_sub("10.101.0.0/24"), _sub("10.101.1.0/24"))
        actions = diff_state(desired, None)

        assert isinstance(actions, list)
        assert all(isinstance(a, Action) for a in actions)
        assert actions[0].kind == "create-network"
        assert "priv-net" in actions[0].target

        subnet_actions = [a for a in actions[1:] if a.kind == "create-subnet"]
        assert len(subnet_actions) == 2
        assert "10.101.0.0/24" in subnet_actions[0].target
        assert "10.101.1.0/24" in subnet_actions[1].target

    def test_network_missing_no_subnets_emits_only_network(self):
        # Boundary: empty desired-subnet tuple. Action stream is just one
        # create-network entry, never an empty list.
        desired = _desired()
        actions = diff_state(desired, None)
        assert len(actions) == 1
        assert actions[0].kind == "create-network"


class TestDiffStateNetworkPresent:
    def test_full_match_all_ok(self):
        desired = _desired(_sub("10.101.0.0/24"), _sub("10.101.1.0/24"))
        current = _mock_network(
            subnets=(
                _mock_subnet("10.101.0.0/24"),
                _mock_subnet("10.101.1.0/24"),
            )
        )
        actions = diff_state(desired, current)
        # Network ok + each subnet ok
        assert all(a.kind == "ok" for a in actions)
        # Three actions: 1 network + 2 subnets
        assert len(actions) == 3

    def test_diverging_network_ip_range_is_drift(self):
        desired = _desired()
        current = _mock_network(ip_range="10.99.0.0/16")
        actions = diff_state(desired, current)

        network_actions = [a for a in actions if "network" in a.target]
        assert any(a.kind == "drift" for a in network_actions)

    def test_drift_skips_subnet_evaluation(self):
        # When the master ip_range is wrong, the planner emits a single
        # drift action and short-circuits — the operator must reconcile
        # the master before subnet diffs are meaningful.
        desired = _desired(_sub("10.101.0.0/24"), _sub("10.101.1.0/24"))
        current = _mock_network(
            ip_range="10.99.0.0/16",
            subnets=(_mock_subnet("10.101.0.0/24"),),
        )
        actions = diff_state(desired, current)
        assert len(actions) == 1
        assert actions[0].kind == "drift"

    def test_subnet_missing_creates_subnet_others_ok(self):
        desired = _desired(
            _sub("10.101.0.0/24"),
            _sub("10.101.1.0/24"),
            _sub("10.101.2.0/24"),
        )
        current = _mock_network(
            subnets=(
                _mock_subnet("10.101.0.0/24"),
                _mock_subnet("10.101.2.0/24"),
            )
        )
        actions = diff_state(desired, current)

        kinds_by_target = {a.target: a.kind for a in actions}
        # Network present, ip_range matches → ok
        net_action = next(a for a in actions if "network" in a.target)
        assert net_action.kind == "ok"
        # Missing subnet → create-subnet
        assert any(a.kind == "create-subnet" and "10.101.1.0/24" in a.target for a in actions), (
            kinds_by_target
        )
        # Existing subnets → ok
        present = [a for a in actions if a.kind == "ok" and "subnet" in a.target.lower()]
        assert len(present) == 2

    def test_subnet_present_with_wrong_zone_is_drift(self):
        desired = _desired(_sub("10.101.0.0/24", zone="eu-central"))
        current = _mock_network(subnets=(_mock_subnet("10.101.0.0/24", network_zone="us-east"),))
        actions = diff_state(desired, current)
        subnet_actions = [a for a in actions if "10.101.0.0/24" in a.target]
        assert subnet_actions, "expected at least one subnet action"
        assert any(a.kind == "drift" for a in subnet_actions)

    def test_subnet_present_matching_is_ok(self):
        desired = _desired(_sub("10.101.0.0/24"))
        current = _mock_network(subnets=(_mock_subnet("10.101.0.0/24"),))
        actions = diff_state(desired, current)
        subnet_actions = [a for a in actions if "10.101.0.0/24" in a.target]
        assert subnet_actions
        assert all(a.kind == "ok" for a in subnet_actions)

    def test_subnet_with_wrong_type_is_drift(self):
        # type='vswitch' instead of 'cloud' — unsupported, so flag as drift.
        desired = _desired(_sub("10.101.0.0/24"))
        current = _mock_network(subnets=(_mock_subnet("10.101.0.0/24", type_="vswitch"),))
        actions = diff_state(desired, current)
        subnet_actions = [a for a in actions if "10.101.0.0/24" in a.target]
        assert any(a.kind == "drift" for a in subnet_actions)

    def test_actual_subnets_none_treated_as_empty(self):
        # Some hcloud responses return ``subnets=None`` when there are no
        # subnets attached. The planner must treat that the same as an
        # empty list (otherwise iterating raises TypeError).
        desired = _desired(_sub("10.101.0.0/24"))
        current = _mock_network()
        current.subnets = None  # override the default empty list
        actions = diff_state(desired, current)
        # Network ok + one missing subnet -> create-subnet
        kinds = [a.kind for a in actions]
        assert "create-subnet" in kinds
