# tests/digitalocean/test_regions.py

"""Tests for ots_shared.digitalocean.regions."""

import pytest

from ots_shared.digitalocean.regions import (
    KNOWN_REGIONS,
    is_known_region,
    regions_from_catalog,
    validate_region,
)


class TestKnownRegions:
    def test_contains_representative_slugs(self):
        assert {"nyc3", "fra1", "sfo3"} <= KNOWN_REGIONS

    def test_is_known_region(self):
        assert is_known_region("nyc3")
        assert not is_known_region("mars1")


class TestValidateRegion:
    def test_accepts_known(self):
        assert validate_region("nyc3") == "nyc3"

    def test_accepts_unknown_but_well_formed(self):
        # The catalog grows; server-side is authoritative.
        assert validate_region("xyz9") == "xyz9"

    @pytest.mark.parametrize("bad", ["", None, 123, []])
    def test_rejects_missing_or_nonstring(self, bad):
        with pytest.raises(SystemExit, match="region slug"):
            validate_region(bad)


class TestRegionsFromCatalog:
    def test_extracts_slugs(self):
        payload = {"regions": [{"slug": "nyc3"}, {"slug": "fra1"}, {"name": "no-slug"}]}
        assert regions_from_catalog(payload) == ["nyc3", "fra1"]

    def test_tolerates_empty_or_malformed(self):
        assert regions_from_catalog({}) == []
        assert regions_from_catalog({"regions": "nope"}) == []
        assert regions_from_catalog("nope") == []
