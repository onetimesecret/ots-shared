# tests/digitalocean/test_paginate.py

"""Tests for ots_shared.digitalocean.paginate."""

import pytest

from ots_shared.digitalocean.paginate import PER_PAGE_MAX, find_by_name, list_all


class TestListAll:
    def test_single_page(self):
        calls = []

        def fetch(*, per_page, page):
            calls.append((per_page, page))
            return {"droplets": [{"id": 1}, {"id": 2}], "links": {}}

        assert list_all(fetch, "droplets") == [{"id": 1}, {"id": 2}]
        assert calls == [(PER_PAGE_MAX, 1)]

    def test_follows_next_link_across_pages(self):
        pages = {
            1: {"droplets": [{"id": 1}], "links": {"pages": {"next": "?page=2"}}},
            2: {"droplets": [{"id": 2}], "links": {"pages": {"next": "?page=3"}}},
            3: {"droplets": [{"id": 3}], "links": {"pages": {}}},
        }

        def fetch(*, per_page, page):
            return pages[page]

        assert [d["id"] for d in list_all(fetch, "droplets")] == [1, 2, 3]

    def test_passes_per_page_override(self):
        def fetch(*, per_page, page):
            assert per_page == 50
            return {"droplets": [], "links": {}}

        assert list_all(fetch, "droplets", per_page=50) == []

    def test_max_pages_guard(self):
        def fetch(*, per_page, page):
            # Never stops advertising a next page.
            return {"droplets": [{"id": page}], "links": {"pages": {"next": "x"}}}

        out = list_all(fetch, "droplets", max_pages=3)
        assert [d["id"] for d in out] == [1, 2, 3]

    def test_missing_key_yields_empty(self):
        assert list_all(lambda **kw: {"links": {}}, "droplets") == []


class TestFindByName:
    def test_single_match(self):
        items = [{"name": "web-01", "id": 7}, {"name": "db-01", "id": 8}]
        assert find_by_name(items, "web-01", kind="droplet") == {"name": "web-01", "id": 7}

    def test_not_found(self):
        with pytest.raises(SystemExit, match="Droplet 'web-99' not found"):
            find_by_name([], "web-99", kind="droplet")

    def test_duplicate_is_fail_loud(self):
        items = [{"name": "web", "id": 1}, {"name": "web", "id": 2}]
        with pytest.raises(SystemExit, match="Multiple droplets named 'web' \\(1, 2\\)"):
            find_by_name(items, "web", kind="droplet")

    def test_custom_id_key(self):
        items = [{"name": "v", "uuid": "a"}, {"name": "v", "uuid": "b"}]
        with pytest.raises(SystemExit, match="a, b"):
            find_by_name(items, "v", kind="volume", id_key="uuid")
