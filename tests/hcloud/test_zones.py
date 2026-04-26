# packages/rots/packages/ots-shared/tests/hcloud/test_zones.py

"""Tests for ots_shared.hcloud.zones constants.

This file is small on purpose: zones is data. The point is to detect
accidental deletions or zone/location decoupling, not to exercise logic.
"""

from __future__ import annotations

import pytest

from ots_shared.hcloud.zones import KNOWN_ZONES, LOCATION_TO_ZONE


class TestKnownZones:
    def test_is_frozenset(self):
        assert isinstance(KNOWN_ZONES, frozenset)

    @pytest.mark.parametrize(
        "zone",
        ["eu-central", "us-east", "us-west", "ap-southeast"],
    )
    def test_expected_zones_present(self, zone):
        # If a zone disappears, network_plan validation breaks for any
        # marker referencing it. Explicit per-zone test makes the failure
        # diagnostic in CI rather than a single set-equality blob.
        assert zone in KNOWN_ZONES


class TestLocationToZone:
    def test_is_dict(self):
        assert isinstance(LOCATION_TO_ZONE, dict)

    @pytest.mark.parametrize(
        "location,expected_zone",
        [
            ("nbg1", "eu-central"),
            ("fsn1", "eu-central"),
            ("hel1", "eu-central"),
            ("ash", "us-east"),
            ("hil", "us-west"),
            ("sin", "ap-southeast"),
        ],
    )
    def test_known_locations_map_to_zones(self, location, expected_zone):
        assert LOCATION_TO_ZONE[location] == expected_zone

    def test_every_location_zone_is_known(self):
        # Drift guard: a location pointing at a zone string not in
        # KNOWN_ZONES would silently bypass marker validation when the
        # user wrote that location.
        unknown = {loc: zone for loc, zone in LOCATION_TO_ZONE.items() if zone not in KNOWN_ZONES}
        assert not unknown, f"locations point at zones not in KNOWN_ZONES: {unknown}"
