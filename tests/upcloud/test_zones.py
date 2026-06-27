# tests/upcloud/test_zones.py

"""Tests for ots_shared.upcloud.zones.

UpCloud zones are atomic (region == zone); there is no location->zone
mapping and no network_zone. These tests guard the known-set and the
validate/unwrap helpers.
"""

from __future__ import annotations

import pytest

from ots_shared.upcloud.zones import (
    KNOWN_ZONES,
    is_known_zone,
    validate_zone,
    zones_from_catalog,
)


class TestKnownZones:
    def test_is_frozenset(self):
        assert isinstance(KNOWN_ZONES, frozenset)

    @pytest.mark.parametrize(
        "zone",
        ["fi-hel1", "fi-hel2", "de-fra1", "nl-ams1", "uk-lon1", "us-nyc1", "sg-sin1"],
    )
    def test_expected_zones_present(self, zone):
        assert zone in KNOWN_ZONES

    def test_no_network_zone_concept(self):
        # Guard against accidentally re-introducing Hetzner-style grouping.
        assert "eu-central" not in KNOWN_ZONES


class TestIsKnownZone:
    def test_known(self):
        assert is_known_zone("de-fra1") is True

    def test_unknown(self):
        assert is_known_zone("xx-zzz9") is False


class TestValidateZone:
    def test_known_zone_returned(self):
        assert validate_zone("de-fra1") == "de-fra1"

    def test_unknown_but_wellformed_accepted(self):
        # Catalog grows; an unknown-but-string id is accepted (server-side
        # is authoritative).
        assert validate_zone("xx-new1") == "xx-new1"

    def test_empty_string_fail_loud(self):
        with pytest.raises(SystemExit, match="zone"):
            validate_zone("")

    def test_none_fail_loud(self):
        with pytest.raises(SystemExit, match="zone"):
            validate_zone(None)

    def test_non_string_fail_loud(self):
        with pytest.raises(SystemExit, match="zone"):
            validate_zone(42)


class TestZonesFromCatalog:
    def test_unwraps_envelope(self):
        payload = {
            "zones": {
                "zone": [
                    {"id": "de-fra1", "description": "Frankfurt", "public": True},
                    {"id": "fi-hel1", "description": "Helsinki", "public": True},
                ]
            }
        }
        assert zones_from_catalog(payload) == ["de-fra1", "fi-hel1"]

    def test_empty_envelope(self):
        assert zones_from_catalog({}) == []

    def test_missing_id_skipped(self):
        payload = {"zones": {"zone": [{"description": "no id"}, {"id": "de-fra1"}]}}
        assert zones_from_catalog(payload) == ["de-fra1"]

    def test_non_dict_input(self):
        assert zones_from_catalog([]) == []  # type: ignore[arg-type]
