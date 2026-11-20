# tests/digitalocean/test_network_plan.py

"""Tests for ots_shared.digitalocean.network_plan (flat-VPC reconciler)."""

from __future__ import annotations

from pathlib import Path

import pytest

from ots_shared.digitalocean.network_plan import (
    DesiredState,
    NetworkSpec,
    diff_state,
    parse_marker,
)

FAKE = Path("/tmp/fake/otsinfra.yaml")


def _marker(**network):
    return {"network": network, "hosts": {}}


class TestParseMarker:
    def test_valid(self):
        state = parse_marker(
            _marker(name="ots", ip_range="10.10.0.0/24", zone="nyc3"),
            marker_path=FAKE,
        )
        assert state == DesiredState(NetworkSpec("ots", "10.10.0.0/24", "nyc3"))

    def _expect_fail(self, marker, capsys, needle):
        # network_plan._fail raises SystemExit(65) and prints the detail to
        # stderr, so the message is asserted there (the exit code is 65).
        with pytest.raises(SystemExit) as exc_info:
            parse_marker(marker, marker_path=FAKE)
        assert exc_info.value.code == 65
        assert needle in capsys.readouterr().err

    def test_missing_network_block(self, capsys):
        self._expect_fail({"hosts": {}}, capsys, "missing top-level 'network:'")

    def test_legacy_network_zone_is_fail_loud(self, capsys):
        marker = _marker(name="ots", ip_range="10.0.0.0/24", network_zone="eu")
        self._expect_fail(marker, capsys, "network.network_zone has been renamed to network.zone")

    def test_legacy_region_is_fail_loud(self, capsys):
        marker = _marker(name="ots", ip_range="10.0.0.0/24", region="nyc3")
        self._expect_fail(marker, capsys, "network.region has been renamed to network.zone")

    def test_ipv6_range_rejected(self, capsys):
        marker = _marker(name="ots", ip_range="fd00::/64", zone="nyc3")
        self._expect_fail(marker, capsys, "IPv4")

    def test_bad_cidr_rejected(self, capsys):
        marker = _marker(name="ots", ip_range="not-a-cidr", zone="nyc3")
        self._expect_fail(marker, capsys, "not a valid CIDR")

    def test_too_small_range_rejected(self, capsys):
        marker = _marker(name="ots", ip_range="10.0.0.0/30", zone="nyc3")
        self._expect_fail(marker, capsys, "/8–/28")

    def test_unknown_region_is_warning_not_fatal(self, capsys):
        parse_marker(
            _marker(name="ots", ip_range="10.0.0.0/24", zone="xyz9"),
            marker_path=FAKE,
        )
        assert "not in the known" in capsys.readouterr().err

    def test_host_pinned_ip_is_fail_loud(self, capsys):
        marker = {
            "network": {"name": "ots", "ip_range": "10.0.0.0/24", "zone": "nyc3"},
            "hosts": {"web": {"private_ip_address": "10.0.0.5"}},
        }
        self._expect_fail(marker, capsys, "assigns private IPs automatically")


class TestDiffState:
    DESIRED = DesiredState(NetworkSpec("ots", "10.10.0.0/24", "nyc3"))

    def test_missing_network_creates(self):
        actions = diff_state(self.DESIRED, None)
        assert [a.kind for a in actions] == ["create-network"]

    def test_matching_range_ok(self):
        current = {"id": "vpc-abc", "ip_range": "10.10.0.0/24"}
        actions = diff_state(self.DESIRED, current)
        assert [a.kind for a in actions] == ["ok"]
        assert "vpc-abc" in actions[0].message

    def test_range_mismatch_is_drift(self):
        current = {"id": "vpc-abc", "ip_range": "10.99.0.0/24"}
        actions = diff_state(self.DESIRED, current)
        assert [a.kind for a in actions] == ["drift"]
        assert "mismatch" in actions[0].message

    def test_reads_ip_range_from_attribute(self):
        current = type("V", (), {"id": "vpc-x", "ip_range": "10.10.0.0/24"})()
        actions = diff_state(self.DESIRED, current)
        assert actions[0].kind == "ok"
