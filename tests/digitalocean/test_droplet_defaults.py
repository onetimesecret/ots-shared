# tests/digitalocean/test_droplet_defaults.py

"""Tests for ots_shared.digitalocean.droplet_defaults.

Mirrors the UpCloud library coverage: marker field resolution (``region`` not
``location``, and NO ``private_ip_*``), the 64 KiB-capped cloud-init loader, the
paginated get_droplet_or_exit, and the §5.4a pinned-IP rejection.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ots_shared.digitalocean.droplet_defaults import (
    MARKER_HOST_FIELDS,
    USER_DATA_LIMIT_BYTES,
    CloudInitPayload,
    get_droplet_or_exit,
    load_cloud_init_user_data,
    resolve_host_defaults,
)

FAKE_MARKER = Path("/tmp/fake/otsinfra.yaml")


def _patched(hosts: dict | None, *, marker_missing: bool = False, env_name: str = "test"):
    """Patch find_marker/load_marker as if the given hosts block was on disk."""
    if marker_missing:
        find = patch("ots_shared.ssh.env.find_marker", return_value=None)
        load = patch("ots_shared.ssh.env.load_marker", return_value={})
    else:
        marker: dict = {"env_name": env_name}
        if hosts is not None:
            marker["hosts"] = hosts
        find = patch("ots_shared.ssh.env.find_marker", return_value=FAKE_MARKER)
        load = patch("ots_shared.ssh.env.load_marker", return_value=marker)
    return find, load


class TestMarkerFieldTable:
    def test_uses_region_not_location(self):
        keys = {f.key for f in MARKER_HOST_FIELDS}
        assert "region" in keys
        assert "location" not in keys
        assert "network_zone" not in keys

    def test_no_private_ip_fields(self):
        keys = {f.key for f in MARKER_HOST_FIELDS}
        assert not any(k.startswith("private_ip") for k in keys)

    def test_unique_keys(self):
        keys = [f.key for f in MARKER_HOST_FIELDS]
        assert len(keys) == len(set(keys))

    def test_limit_is_64_kib(self):
        assert USER_DATA_LIMIT_BYTES == 64 * 1024


class TestResolveHostDefaults:
    def test_returns_none_when_no_marker(self):
        find, load = _patched(None, marker_missing=True)
        with find, load:
            assert resolve_host_defaults(None, "web-01") is None

    def test_resolves_region_and_size(self):
        hosts = {"web": {"server_type": "s-2vcpu-4gb", "image": "debian-13-x64", "region": "fra1"}}
        find, load = _patched(hosts)
        with find, load:
            defaults = resolve_host_defaults("web", "web-01")
        assert defaults is not None
        assert defaults.values["region"] == "fra1"
        assert defaults.values["server_type"] == "s-2vcpu-4gb"

    def test_pinned_ip_is_fail_loud(self):
        hosts = {"web": {"region": "fra1", "private_ip_address": "10.0.0.5"}}
        find, load = _patched(hosts)
        with find, load:
            with pytest.raises(SystemExit, match="assigns private IPs automatically"):
                resolve_host_defaults("web", "web-01")

    def test_pinned_ip_formula_is_fail_loud(self):
        hosts = {"web": {"region": "fra1", "private_ip_formula": "base+ordinal"}}
        find, load = _patched(hosts)
        with find, load:
            with pytest.raises(SystemExit, match="private_ip"):
                resolve_host_defaults("web", "web-01")

    def test_unknown_key_is_fail_loud(self):
        hosts = {"web": {"region": "fra1", "bogus": "x"}}
        find, load = _patched(hosts)
        with find, load:
            with pytest.raises(SystemExit, match="unknown key"):
                resolve_host_defaults("web", "web-01")

    def test_no_hosts_block_fail_loud(self):
        find, load = _patched(None)
        with find, load:
            with pytest.raises(SystemExit, match="no 'hosts' block"):
                resolve_host_defaults("web", "web-01")


class TestGetDropletOrExit:
    def _client(self, droplets):
        client = MagicMock()
        client.droplets.list.return_value = {"droplets": droplets, "links": {}}
        return client

    def test_found(self):
        client = self._client([{"name": "web-01", "id": 5}])
        assert get_droplet_or_exit(client, "web-01")["id"] == 5

    def test_not_found_fail_loud(self):
        client = self._client([])
        with pytest.raises(SystemExit, match="not found"):
            get_droplet_or_exit(client, "web-01")

    def test_duplicate_fail_loud(self):
        client = self._client([{"name": "web", "id": 1}, {"name": "web", "id": 2}])
        with pytest.raises(SystemExit, match="Multiple droplets"):
            get_droplet_or_exit(client, "web")

    def test_passes_server_side_name_filter(self):
        client = self._client([{"name": "web-01", "id": 5}])
        get_droplet_or_exit(client, "web-01")
        assert client.droplets.list.call_args.kwargs["name"] == "web-01"


class TestLoadCloudInit:
    def test_none_when_no_source(self):
        assert load_cloud_init_user_data(None, None) is None

    def test_mutually_exclusive(self, tmp_path):
        f = tmp_path / "ci.yaml"
        f.write_text("x")
        with pytest.raises(SystemExit, match="mutually exclusive"):
            load_cloud_init_user_data(f, "echo hi")

    def test_reads_file(self, tmp_path):
        f = tmp_path / "ci.yaml"
        f.write_text("#cloud-config\n")
        payload = load_cloud_init_user_data(f)
        assert isinstance(payload, CloudInitPayload)
        assert payload.user_data == "#cloud-config\n"
        assert payload.raw_size == payload.payload_size

    def test_missing_file_fail_loud(self, tmp_path):
        with pytest.raises(SystemExit, match="not found"):
            load_cloud_init_user_data(tmp_path / "nope.yaml")

    def test_over_limit_rejected(self, tmp_path):
        f = tmp_path / "big.yaml"
        f.write_text("x" * (USER_DATA_LIMIT_BYTES + 1))
        with pytest.raises(SystemExit, match="exceeds 64 KiB"):
            load_cloud_init_user_data(f)

    def test_command_stdout(self):
        payload = load_cloud_init_user_data(None, "printf '#cloud-config'")
        assert payload is not None
        assert "#cloud-config" in payload.user_data

    def test_command_failure_fail_loud(self):
        with pytest.raises(SystemExit, match="Cloud-init command failed"):
            load_cloud_init_user_data(None, "exit 3")

    def test_command_empty_output_fail_loud(self):
        with pytest.raises(SystemExit, match="no output"):
            load_cloud_init_user_data(None, "true")
