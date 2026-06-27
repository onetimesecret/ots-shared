# tests/upcloud/test_server_defaults.py

"""Tests for ots_shared.upcloud.server_defaults.

Mirrors the hcloud library coverage: marker field resolution (with
``region`` instead of ``location``), the no-cap cloud-init loader, and the
list-based get_server_or_exit (UpCloud has no get-by-name).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ots_shared.upcloud.server_defaults import (
    MARKER_HOST_FIELDS,
    USER_DATA_LIMIT_BYTES,
    CloudInitPayload,
    HostDefaults,
    format_traffic,
    get_server_or_exit,
    load_cloud_init_user_data,
    marker_network_name_upcloud,
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


# ---------------------------------------------------------------------------
# Marker field table
# ---------------------------------------------------------------------------


class TestMarkerFieldTable:
    def test_uses_region_not_location(self):
        keys = {f.key for f in MARKER_HOST_FIELDS}
        assert "region" in keys
        assert "location" not in keys
        assert "network_zone" not in keys

    def test_private_ip_fields_present(self):
        keys = {f.key for f in MARKER_HOST_FIELDS}
        assert {
            "private_ip_address",
            "private_ip_cidr",
            "private_ip_assignment_type",
            "private_ip_formula",
        }.issubset(keys)

    def test_unique_keys(self):
        keys = [f.key for f in MARKER_HOST_FIELDS]
        assert len(keys) == len(set(keys))


# ---------------------------------------------------------------------------
# resolve_host_defaults
# ---------------------------------------------------------------------------


class TestResolveHostDefaults:
    def test_no_marker_returns_none(self):
        find, load = _patched(None, marker_missing=True)
        with find, load:
            assert resolve_host_defaults(role=None, name="web-prod") is None

    def test_missing_hosts_block_fail_loud(self):
        find, load = _patched(None)
        with find, load:
            with pytest.raises(SystemExit, match="no 'hosts' block"):
                resolve_host_defaults(role=None, name="web-prod")

    def test_ssh_env_import_unavailable_returns_none(self):
        # When ots_shared.ssh.env cannot be imported (slimmed install), the
        # resolver degrades to None rather than crashing (lines ~95-96).
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "ots_shared.ssh.env":
                raise ImportError("ssh extras not installed")
            return real_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", side_effect=fake_import):
            assert resolve_host_defaults(role=None, name="web-prod") is None

    def test_empty_hosts_block_fail_loud(self):
        # 'hosts' present but empty → distinct fail-loud message (line ~112).
        find, load = _patched({})
        with find, load:
            with pytest.raises(SystemExit, match="'hosts' block is empty"):
                resolve_host_defaults(role=None, name="web-01")

    def test_host_not_a_mapping_fail_loud(self):
        # An explicit role resolving to a non-dict host value fails loud
        # (line ~119).
        find, load = _patched({"web": "not-a-mapping"})
        with find, load:
            with pytest.raises(SystemExit, match="hosts.web must be a mapping"):
                resolve_host_defaults(role="web", name="web-01")

    def test_region_str_resolves(self):
        find, load = _patched({"web": {"server_type": "2xCPU-4GB", "region": "de-fra1"}})
        with find, load:
            result = resolve_host_defaults(role="web", name="web-01")
        assert isinstance(result, HostDefaults)
        assert result.get("server_type") == "2xCPU-4GB"
        assert result.get("region") == "de-fra1"

    def test_location_key_is_unknown_for_upcloud(self):
        # A Hetzner-shaped marker with `location:` must fail loud here,
        # since UpCloud uses `region`.
        find, load = _patched({"web": {"location": "nbg1"}})
        with find, load:
            with pytest.raises(SystemExit, match="unknown key"):
                resolve_host_defaults(role="web", name="web-01")

    def test_private_ip_fields_resolve(self):
        find, load = _patched(
            {
                "db": {
                    "region": "de-fra1",
                    "private_ip_address": "10.0.0.11",
                    "private_ip_cidr": "10.0.0.0/24",
                }
            }
        )
        with find, load:
            result = resolve_host_defaults(role="db", name="db-01")
        assert result is not None
        assert result.get("private_ip_address") == "10.0.0.11"
        assert result.get("private_ip_cidr") == "10.0.0.0/24"

    def test_type_mismatch_fail_loud(self):
        find, load = _patched({"web": {"server_type": ["x"]}})
        with find, load:
            with pytest.raises(SystemExit, match="server_type.*must be str"):
                resolve_host_defaults(role="web", name="web-01")

    def test_firewalls_list_and_backup_bool(self):
        find, load = _patched({"web": {"firewalls": ["a", "b"], "backup": True}})
        with find, load:
            result = resolve_host_defaults(role="web", name="web-01")
        assert result is not None
        assert result.get("firewalls") == ["a", "b"]
        assert result.get("backup") is True

    def test_missing_list_field_is_empty(self):
        find, load = _patched({"web": {"region": "de-fra1"}})
        with find, load:
            result = resolve_host_defaults(role="web", name="web-01")
        assert result is not None
        assert result.get("firewalls") == []

    def test_dict_foreign_section_ignored(self):
        find, load = _patched({"web": {"region": "de-fra1", "unce": {"files": []}}})
        with find, load:
            result = resolve_host_defaults(role="web", name="web-01")
        assert result is not None
        assert "unce" not in result.values


# ---------------------------------------------------------------------------
# load_cloud_init_user_data — no size cap
# ---------------------------------------------------------------------------


class TestLoadCloudInit:
    def test_user_data_limit_is_none(self):
        assert USER_DATA_LIMIT_BYTES is None

    def test_loads_file(self, tmp_path):
        ci = tmp_path / "cloud-init.yaml"
        ci.write_text("#cloud-config\npackage_update: true\n")
        payload = load_cloud_init_user_data(ci)
        assert isinstance(payload, CloudInitPayload)
        assert payload.user_data.startswith("#cloud-config")
        assert payload.raw_size == payload.payload_size

    def test_huge_payload_not_rejected(self, tmp_path):
        # UpCloud has no client-side cap: a 1 MiB payload must pass through.
        ci = tmp_path / "huge.yaml"
        ci.write_text("#cloud-config\n" + ("x" * (1024 * 1024)))
        payload = load_cloud_init_user_data(ci)
        assert payload is not None
        assert payload.payload_size > 1024 * 1024

    def test_soft_warning_does_not_reject(self, tmp_path, caplog):
        # Even with a soft max_bytes, the loader warns but never rejects.
        import logging

        ci = tmp_path / "big.yaml"
        ci.write_text("x" * 5000)
        with caplog.at_level(logging.WARNING):
            payload = load_cloud_init_user_data(ci, max_bytes=1000)
        assert payload is not None
        assert payload.payload_size == 5000

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(SystemExit, match="not found"):
            load_cloud_init_user_data(tmp_path / "nope.yaml")

    def test_neither_source_returns_none(self):
        assert load_cloud_init_user_data(None) is None

    def test_path_and_cmd_mutually_exclusive(self, tmp_path):
        ci = tmp_path / "ci.yaml"
        ci.write_text("#cloud-config\n")
        with pytest.raises(SystemExit, match="mutually exclusive"):
            load_cloud_init_user_data(ci, cmd="echo hi")

    def test_command_output_used(self):
        payload = load_cloud_init_user_data(None, cmd="echo '#cloud-config'")
        assert payload is not None
        assert "#cloud-config" in payload.user_data

    def test_command_failure_raises(self):
        with pytest.raises(SystemExit, match="failed"):
            load_cloud_init_user_data(None, cmd="exit 7")

    def test_command_failure_surfaces_stderr(self, capsys):
        # A failing command with stderr output echoes that stderr before the
        # SystemExit (line ~228), so the operator sees the underlying error.
        with pytest.raises(SystemExit, match="failed"):
            load_cloud_init_user_data(None, cmd="echo boom-detail >&2; exit 3")
        assert "boom-detail" in capsys.readouterr().err

    def test_empty_command_output_raises(self):
        with pytest.raises(SystemExit, match="no output"):
            load_cloud_init_user_data(None, cmd="true")


# ---------------------------------------------------------------------------
# get_server_or_exit — list + client-side hostname filter
# ---------------------------------------------------------------------------


class TestGetServerOrExit:
    def test_found_by_hostname(self):
        mgr = MagicMock()
        srv = MagicMock(hostname="web-01", uuid="u1")
        mgr.get_servers.return_value = [MagicMock(hostname="db-01"), srv]
        assert get_server_or_exit(mgr, "web-01") is srv

    def test_not_found_fail_loud(self):
        mgr = MagicMock()
        mgr.get_servers.return_value = [MagicMock(hostname="db-01")]
        with pytest.raises(SystemExit, match="not found"):
            get_server_or_exit(mgr, "web-01")

    def test_duplicate_hostname_fail_loud(self):
        mgr = MagicMock()
        mgr.get_servers.return_value = [
            MagicMock(hostname="web-01", uuid="u1"),
            MagicMock(hostname="web-01", uuid="u2"),
        ]
        with pytest.raises(SystemExit, match="Multiple servers"):
            get_server_or_exit(mgr, "web-01")


# ---------------------------------------------------------------------------
# marker_network_name_upcloud — thin alias over the neutral helper
# ---------------------------------------------------------------------------


class TestMarkerNetworkNameUpcloud:
    def test_delegates_to_neutral_helper(self):
        # The UpCloud alias is a pass-through over the provider-neutral
        # marker_network_name; it forwards the path and returns the result
        # verbatim (line ~153).
        with patch(
            "ots_shared.upcloud.server_defaults.marker_network_name",
            return_value="priv-net",
        ) as neutral:
            assert marker_network_name_upcloud(FAKE_MARKER) == "priv-net"
        neutral.assert_called_once_with(FAKE_MARKER)


# ---------------------------------------------------------------------------
# format_traffic (re-exported neutral helper)
# ---------------------------------------------------------------------------


class TestFormatTraffic:
    def test_zero(self):
        assert format_traffic(0) == "0 B"

    def test_kib(self):
        assert format_traffic(1024) == "1.0 KB"
