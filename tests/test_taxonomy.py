"""Tests for ots_shared.taxonomy module."""

from ots_shared.taxonomy import KNOWN_ENVIRONMENTS, KNOWN_JURISDICTIONS


class TestKnownJurisdictions:
    """Verify jurisdiction vocabulary properties."""

    def test_is_frozenset(self):
        assert isinstance(KNOWN_JURISDICTIONS, frozenset)

    def test_contains_major_codes(self):
        for code in ("us", "eu", "ca", "nz", "au", "uk", "de", "fr", "jp"):
            assert code in KNOWN_JURISDICTIONS

    def test_all_entries_are_lowercase(self):
        for code in KNOWN_JURISDICTIONS:
            assert code == code.lower()

    def test_all_entries_are_two_letters(self):
        for code in KNOWN_JURISDICTIONS:
            assert len(code) == 2

    def test_no_duplicates(self):
        as_list = list(KNOWN_JURISDICTIONS)
        assert len(as_list) == len(set(as_list))

    def test_count_matches_expected(self):
        assert len(KNOWN_JURISDICTIONS) >= 38


class TestKnownEnvironments:
    """Verify environment vocabulary properties."""

    def test_is_dict(self):
        assert isinstance(KNOWN_ENVIRONMENTS, dict)

    def test_canonical_names_are_idempotent(self):
        for canonical in ("prod", "staging", "dev", "test", "qa", "uat", "demo", "sandbox", "lab"):
            assert KNOWN_ENVIRONMENTS[canonical] == canonical

    def test_aliases_map_to_canonical(self):
        assert KNOWN_ENVIRONMENTS["production"] == "prod"
        assert KNOWN_ENVIRONMENTS["stg"] == "staging"
        assert KNOWN_ENVIRONMENTS["stage"] == "staging"
        assert KNOWN_ENVIRONMENTS["development"] == "dev"
        assert KNOWN_ENVIRONMENTS["testing"] == "test"

    def test_all_values_are_strings(self):
        for key, val in KNOWN_ENVIRONMENTS.items():
            assert isinstance(key, str)
            assert isinstance(val, str)

    def test_all_values_are_lowercase(self):
        for val in KNOWN_ENVIRONMENTS.values():
            assert val == val.lower()
