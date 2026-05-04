# tests/hcloud/test_server_defaults.py

"""Tests for ots_shared.hcloud.server_defaults.

Library-layer coverage moved out of lots' test_commands/test_helpers.py.
The CLI-layer precedence and end-to-end firewall tests stay in lots since
they exercise lots.hcloud.commands.server.app.
"""

from __future__ import annotations

import gzip
from pathlib import Path
from unittest.mock import patch

import pytest

from ots_shared.hcloud.server_defaults import (
    MARKER_HOST_FIELDS,
    USER_DATA_LIMIT_BYTES,
    CloudInitPayload,
    HostDefaults,
    format_traffic,
    load_cloud_init_user_data,
    marker_network_name,
    resolve_host_defaults,
)

# ---------------------------------------------------------------------------
# format_traffic
# ---------------------------------------------------------------------------


class TestFormatTraffic:
    def test_none_returns_zero(self):
        assert format_traffic(None) == "0 B"

    def test_zero_returns_zero(self):
        assert format_traffic(0) == "0 B"

    def test_bytes(self):
        assert format_traffic(1) == "1.0 B"
        assert format_traffic(1023) == "1023.0 B"

    def test_kilobytes(self):
        # Boundary: exactly 1 KiB jumps to KB.
        assert format_traffic(1024) == "1.0 KB"

    def test_megabytes(self):
        assert format_traffic(1048576) == "1.0 MB"

    def test_gigabytes(self):
        assert format_traffic(1073741824) == "1.0 GB"

    def test_terabytes(self):
        assert format_traffic(1099511627776) == "1.0 TB"

    def test_petabytes(self):
        assert format_traffic(1125899906842624) == "1.0 PB"

    def test_fractional_kilobytes(self):
        assert format_traffic(1536) == "1.5 KB"

    def test_fractional_megabytes(self):
        assert format_traffic(int(2.5 * 1024 * 1024)) == "2.5 MB"

    def test_fractional_gigabytes(self):
        assert format_traffic(int(3.25 * 1024**3)) == "3.2 GB"

    def test_just_under_one_kib(self):
        # Boundary just below 1024 must still report bytes.
        assert format_traffic(1023) == "1023.0 B"

    def test_negative_is_treated_as_falsy(self):
        # -1 is truthy under `if not bytes_val`, but the iteration would
        # quickly emit a non-sensical "neg PB". This is documenting the
        # current behavior so a future hardening change is explicit.
        result = format_traffic(-1)
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# resolve_host_defaults
# ---------------------------------------------------------------------------
#
# The resolver pulls two functions out of ots_shared.ssh.env:
#     find_marker()  -> Path | None
#     load_marker(p) -> dict
# We patch both at the import site inside resolve_host_defaults so tests can
# stage a synthetic marker without touching the filesystem walk-up logic.


FAKE_MARKER = Path("/tmp/fake/otsinfra.yaml")


def _patched(hosts: dict | None, *, marker_missing: bool = False, env_name: str = "test"):
    """Patch find_marker/load_marker as if the given hosts block was on disk.

    ``marker_missing=True`` simulates no marker file found (find_marker -> None).
    ``hosts=None`` simulates a marker file present but no 'hosts' key.
    Otherwise ``hosts`` is placed under a top-level ``hosts`` key.

    The marker always carries a synthetic ``env_name`` (default ``"test"``) so
    the canonical hostname parser passes its env-name validation. Tests that
    drive auto-match must use a hostname whose prefix equals ``env_name``.
    """
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


class TestResolveHostDefaultsNoMarker:
    def test_no_marker_file_returns_none(self):
        find, load = _patched(None, marker_missing=True)
        with find, load:
            result = resolve_host_defaults(role=None, name="web-prod")
        assert result is None


class TestResolveHostDefaultsFailLoud:
    def test_missing_hosts_block_is_distinct_error(self):
        # Marker file present but no 'hosts' key — different message from
        # "no marker file found" so the operator can tell the two apart.
        find, load = _patched(None)
        with find, load:
            with pytest.raises(SystemExit, match="no 'hosts' block"):
                resolve_host_defaults(role=None, name="web-prod")

    def test_empty_hosts_block_raises(self):
        find, load = _patched({})
        with find, load:
            with pytest.raises(SystemExit, match="'hosts' block is empty"):
                resolve_host_defaults(role=None, name="web-prod")

    def test_explicit_role_not_declared(self):
        find, load = _patched({"web": {"server_type": "cx11"}})
        with find, load:
            with pytest.raises(SystemExit, match="role 'db' not declared"):
                resolve_host_defaults(role="db", name="whatever")

    def test_auto_match_zero_matches_raises(self):
        find, load = _patched({"web": {"server_type": "cx11"}})
        with find, load:
            # New canonical parser: error wording is "matches no role" (parser
            # phrasing) carried through to the SystemExit message.
            with pytest.raises(SystemExit, match="matches no role"):
                resolve_host_defaults(role=None, name="test-foo-bar")

    def test_auto_match_longest_suffix_wins(self):
        # Under the canonical parser the auto-match is *not* ambiguous —
        # the longest-suffix that matches a hosts key wins deterministically.
        # ``test-prod-web-01`` against ``{web, prod-web}`` resolves to
        # ``prod-web`` (longer suffix) instead of ``web``.
        find, load = _patched({"web": {"server_type": "cx11"}, "prod-web": {"server_type": "cx22"}})
        with find, load:
            result = resolve_host_defaults(role=None, name="test-prod-web-01")
        assert result is not None
        assert result.role == "prod-web"
        assert result.get("server_type") == "cx22"

    def test_unknown_scalar_key_raises_with_allowed_list(self):
        # Typo: `server-type` (hyphen) vs `server_type` (underscore). This
        # used to fall through silently and hand the hardcoded default back.
        find, load = _patched({"web": {"server-type": "cx11"}})
        with find, load:
            with pytest.raises(SystemExit, match="unknown key") as excinfo:
                resolve_host_defaults(role=None, name="test-web-01")
        # Error should enumerate the allowed keys so the user can spot the fix.
        assert "server_type" in str(excinfo.value)

    def test_unknown_dict_key_is_silently_ignored(self):
        # Foreign-tool sections (rots' `unce:`, `caddy:` file provisioning)
        # are dict-valued under hosts.<role>. The resolver must not validate
        # them — they belong to other tools and will evolve independently.
        find, load = _patched(
            {
                "web": {
                    "server_type": "cpx32",
                    "unce": {"files": [{"source": "/x", "destination": "/y"}]},
                    "caddy": {"files": [{"destination": "/z", "content": "..."}]},
                }
            }
        )
        with find, load:
            result = resolve_host_defaults(role="web", name="web-01")
        assert result is not None
        assert result.get("server_type") == "cpx32"
        # Foreign sections must NOT leak into HostDefaults.values.
        assert "unce" not in result.values
        assert "caddy" not in result.values

    def test_type_mismatch_str_field(self):
        # A list under a str field should fail loud, not be silently dropped.
        find, load = _patched({"web": {"server_type": ["cx11"]}})
        with find, load:
            with pytest.raises(SystemExit, match="server_type.*must be str"):
                resolve_host_defaults(role=None, name="test-web-01")

    def test_type_mismatch_int_under_bool_field(self):
        # bool is a subclass of int in Python — but a YAML `1` under a bool
        # field is still rejected, otherwise accidental 0/1 typing would
        # silently disable/enable backups.
        find, load = _patched({"web": {"backup": 1}})
        with find, load:
            with pytest.raises(SystemExit, match="backup.*must be bool"):
                resolve_host_defaults(role=None, name="test-web-01")

    def test_error_message_cites_file_role_key(self):
        find, load = _patched({"web": {"server_type": 42}})
        with find, load:
            with pytest.raises(SystemExit) as excinfo:
                resolve_host_defaults(role=None, name="test-web-01")
        msg = str(excinfo.value)
        # file path, role, key, flag, expected type, got type all present
        assert str(FAKE_MARKER) in msg
        assert "hosts.web.server_type" in msg
        assert "--server-type" in msg
        assert "str" in msg

    def test_role_value_must_be_dict(self):
        # hosts.web is a string instead of a mapping — fail loud.
        find, load = _patched({"web": "not-a-dict"})
        with find, load:
            with pytest.raises(SystemExit, match="must be a mapping"):
                resolve_host_defaults(role="web", name="web-01")


class TestResolveHostDefaultsCoercion:
    def test_str_roundtrip(self):
        find, load = _patched(
            {"web": {"server_type": "cx22", "image": "debian-13", "location": "fsn1"}}
        )
        with find, load:
            result = resolve_host_defaults(role="web", name="web-01")
        assert isinstance(result, HostDefaults)
        assert result.role == "web"
        assert result.marker_path == FAKE_MARKER
        assert result.get("server_type") == "cx22"
        assert result.get("image") == "debian-13"
        assert result.get("location") == "fsn1"

    def test_list_str_roundtrip(self):
        find, load = _patched({"web": {"firewalls": ["web-fw", "default-fw"]}})
        with find, load:
            result = resolve_host_defaults(role="web", name="web-01")
        assert result is not None
        assert result.get("firewalls") == ["web-fw", "default-fw"]

    def test_list_str_from_scalar_string(self):
        # A single scalar string under a list field is promoted to a
        # one-element list. Convenience for the "just one firewall" case.
        find, load = _patched({"web": {"firewalls": "only-fw"}})
        with find, load:
            result = resolve_host_defaults(role="web", name="web-01")
        assert result is not None
        assert result.get("firewalls") == ["only-fw"]

    def test_list_str_rejects_non_string_elements(self):
        find, load = _patched({"web": {"firewalls": ["web-fw", 42]}})
        with find, load:
            with pytest.raises(SystemExit, match="firewalls.*must be list"):
                resolve_host_defaults(role="web", name="web-01")

    def test_bool_roundtrip_true(self):
        find, load = _patched({"web": {"backup": True}})
        with find, load:
            result = resolve_host_defaults(role="web", name="web-01")
        assert result is not None
        assert result.get("backup") is True

    def test_bool_roundtrip_false(self):
        find, load = _patched({"web": {"backup": False}})
        with find, load:
            result = resolve_host_defaults(role="web", name="web-01")
        assert result is not None
        assert result.get("backup") is False

    def test_missing_str_key_returns_none(self):
        find, load = _patched({"web": {"server_type": "cx11"}})
        with find, load:
            result = resolve_host_defaults(role="web", name="web-01")
        assert result is not None
        assert result.get("image") is None

    def test_missing_list_key_returns_empty_list(self):
        # Lisp-style: list-kind fields are ALWAYS list[T]. Missing YAML key
        # surfaces as []; consumers iterate without branching on None.
        find, load = _patched({"web": {"server_type": "cx11"}})
        with find, load:
            result = resolve_host_defaults(role="web", name="web-01")
        assert result is not None
        assert result.get("firewalls") == []

    def test_missing_list_key_when_no_host_keys(self):
        # Even when hosts.<role> is {} (role declared but empty), list-kind
        # fields still resolve to [] — never None.
        find, load = _patched({"web": {}})
        with find, load:
            result = resolve_host_defaults(role="web", name="web-01")
        assert result is not None
        assert result.get("firewalls") == []

    def test_profile_str_field_accepted(self):
        # Schema-level acceptance: a non-canonical role (e.g. ``web_alt``)
        # carrying ``profile: web_alt`` validates without an unknown-key
        # error. Dispatch at cloud-init render time is a separate layer.
        find, load = _patched({"web_alt": {"profile": "web_alt"}})
        with find, load:
            result = resolve_host_defaults(role="web_alt", name="web-alt-01")
        assert result is not None
        assert result.get("profile") == "web_alt"

    def test_profile_must_be_string(self):
        find, load = _patched({"web_alt": {"profile": 42}})
        with find, load:
            with pytest.raises(SystemExit, match="profile.*must be str"):
                resolve_host_defaults(role="web_alt", name="web-alt-01")

    def test_profile_must_be_non_empty(self):
        find, load = _patched({"web_alt": {"profile": ""}})
        with find, load:
            with pytest.raises(SystemExit, match="profile.*must be str"):
                resolve_host_defaults(role="web_alt", name="web-alt-01")


class TestResolveHostDefaultsRoleMatching:
    def test_explicit_role_wins_over_auto_match(self):
        # Even if name tokens would auto-match 'web', explicit --role=db is used.
        find, load = _patched({"web": {"server_type": "cx11"}, "db": {"server_type": "cx22"}})
        with find, load:
            result = resolve_host_defaults(role="db", name="web-01")
        assert result is not None
        assert result.role == "db"
        assert result.get("server_type") == "cx22"

    def test_auto_match_single(self):
        find, load = _patched({"web": {"server_type": "cx11"}})
        with find, load:
            result = resolve_host_defaults(role=None, name="test-web-01")
        assert result is not None
        assert result.role == "web"


class TestMarkerFieldTable:
    def test_table_entries_unique_keys(self):
        keys = [f.key for f in MARKER_HOST_FIELDS]
        assert len(keys) == len(set(keys)), "duplicate marker keys in table"

    def test_table_entries_have_flag_and_kind(self):
        # Sanity: every row is fully populated. A half-filled row would let
        # a flag "appear" in the allowlist without participating in coercion.
        for field in MARKER_HOST_FIELDS:
            assert field.key
            assert field.flag.startswith("--")
            assert field.kind in (str, list, bool, int)


# ---------------------------------------------------------------------------
# End-to-end trace against the real examples/environment/otsinfra.yaml shape
# ---------------------------------------------------------------------------
#
# Library-only subset. The CLI test that drives `lots.hcloud.commands.server.app.create`
# stays in lots/tests/hcloud/test_commands/test_helpers.py.


PROFILES_FIXTURE = {
    "db": {
        "private_ip_address": "10.0.0.11",
        "server_type": "cpx42",
        "image": "debian-13",
        "location": "nbg1",
    },
    "web": {
        "private_ip_address": "10.0.0.21",
        "server_type": "cpx32",
        "image": "debian-13",
        "location": "nbg1",
        # Foreign-tool sections (rots' file provisioning). Dict-valued,
        # so the resolver must silently ignore them.
        "unce": {
            "files": [
                {
                    "source": "/abs/path/Caddyfile.template",
                    "destination": "/etc/onetimesecret/Caddyfile.template",
                    "owner": "unce",
                }
            ]
        },
        "caddy": {
            "files": [
                {
                    "destination": "/etc/caddy/.caddy.env",
                    "owner": "caddy",
                    "content": "CLOUDFLARE_API_TOKEN=${CLOUDFLARE_API_TOKEN}\n",
                }
            ]
        },
    },
    "jumphost": {
        "private_ip_address": "10.0.0.5",
        "server_type": "cpx12",
    },
}


class TestProfilesFixtureResolution:
    # When --role is supplied the parser is bypassed (explicit wins), so
    # the env_name pinned in the patched marker is irrelevant for these
    # cases. Kept at "profiles" so the synthetic marker still resembles
    # the real examples/environment/otsinfra.yaml shape.

    def test_web_role_pulls_four_str_defaults(self):
        # Resolve `web` and assert the 4 str per-host marker fields
        # come through. Network name is no longer per-host — it lives
        # in the top-level `network:` block (read separately via
        # marker_network_name()).
        find, load = _patched(PROFILES_FIXTURE, env_name="profiles")
        with find, load:
            result = resolve_host_defaults(role="web", name="profiles-web-01")
        assert result is not None
        assert result.role == "web"
        assert result.get("server_type") == "cpx32"
        assert result.get("image") == "debian-13"
        assert result.get("location") == "nbg1"
        assert result.get("private_ip_address") == "10.0.0.21"

    def test_web_role_omits_foreign_tool_sections(self):
        find, load = _patched(PROFILES_FIXTURE, env_name="profiles")
        with find, load:
            result = resolve_host_defaults(role="web", name="profiles-web-01")
        assert result is not None
        # unce/caddy are dict-valued — must not appear in HostDefaults.
        assert "unce" not in result.values
        assert "caddy" not in result.values
        # Nothing surprising leaks through: keys are the allowed scalar
        # keys present in YAML, plus the seeded list-kind keys ([]).
        expected_scalar = {"server_type", "image", "location", "private_ip_address"}
        assert expected_scalar.issubset(result.values.keys())

    def test_web_role_firewalls_default_empty(self):
        # Profiles YAML does not list `firewalls:` under hosts.web —
        # resolver seeds it as [] so the CLI --firewall stacking path
        # (which today happens independently of marker) can later be
        # wired up without a None check.
        find, load = _patched(PROFILES_FIXTURE, env_name="profiles")
        with find, load:
            result = resolve_host_defaults(role="web", name="profiles-web-01")
        assert result is not None
        assert result.get("firewalls") == []


# ---------------------------------------------------------------------------
# marker_network_name
# ---------------------------------------------------------------------------


def _patched_with_network(network_block: dict | None, *, marker_missing: bool = False):
    """Patch find_marker/load_marker to stage a marker with a network block."""
    if marker_missing:
        find = patch("ots_shared.ssh.env.find_marker", return_value=None)
        load = patch("ots_shared.ssh.env.load_marker", return_value={})
    else:
        marker = {}
        if network_block is not None:
            marker["network"] = network_block
        find = patch("ots_shared.ssh.env.find_marker", return_value=FAKE_MARKER)
        load = patch("ots_shared.ssh.env.load_marker", return_value=marker)
    return find, load


class TestMarkerNetworkName:
    def test_no_marker_returns_none(self):
        find, load = _patched_with_network(None, marker_missing=True)
        with find, load:
            assert marker_network_name() is None

    def test_no_network_block_returns_none(self):
        find, load = _patched_with_network(None)
        with find, load:
            assert marker_network_name() is None

    def test_happy_path_returns_name(self):
        find, load = _patched_with_network(
            {
                "name": "priv-net",
                "ip_range": "10.101.0.0/16",
                "network_zone": "eu-central",
            }
        )
        with find, load:
            assert marker_network_name() == "priv-net"

    def test_malformed_network_block_raises(self):
        # network is not a dict — get_network() raises TypeError, which
        # marker_network_name re-raises as SystemExit (fail loud).
        find, load = _patched_with_network({})  # placeholder
        # Override the load mock to return a non-dict network block.
        with (
            patch("ots_shared.ssh.env.find_marker", return_value=FAKE_MARKER),
            patch(
                "ots_shared.ssh.env.load_marker",
                return_value={"network": "not-a-dict"},
            ),
        ):
            with pytest.raises(SystemExit):
                marker_network_name()

    def test_explicit_marker_path_overrides_walkup(self):
        # When marker_path is supplied, find_marker is not called.
        with (
            patch("ots_shared.ssh.env.find_marker") as find_mock,
            patch(
                "ots_shared.ssh.env.load_marker",
                return_value={
                    "network": {
                        "name": "explicit-net",
                        "ip_range": "10.50.0.0/16",
                        "network_zone": "eu-central",
                    }
                },
            ),
        ):
            result = marker_network_name(marker_path=Path("/explicit/otsinfra.yaml"))
        assert result == "explicit-net"
        find_mock.assert_not_called()


# ---------------------------------------------------------------------------
# load_cloud_init_user_data
# ---------------------------------------------------------------------------


class TestLoadCloudInitFromFile:
    def test_loads_yaml_content(self, tmp_path):
        ci = tmp_path / "cloud-init.yaml"
        ci.write_text("#cloud-config\npackage_update: true\n")
        payload = load_cloud_init_user_data(ci)
        assert payload is not None
        assert isinstance(payload, CloudInitPayload)
        assert payload.user_data.startswith("#cloud-config")
        assert payload.gzipped is False
        assert payload.raw_size > 0
        assert payload.payload_size == payload.raw_size

    def test_missing_file_raises(self, tmp_path):
        missing = tmp_path / "does-not-exist.yaml"
        with pytest.raises(SystemExit, match="not found"):
            load_cloud_init_user_data(missing)

    def test_neither_path_nor_cmd_returns_none(self):
        assert load_cloud_init_user_data(None) is None
        assert load_cloud_init_user_data(None, cmd=None) is None

    def test_path_and_cmd_mutually_exclusive(self, tmp_path):
        ci = tmp_path / "cloud-init.yaml"
        ci.write_text("#cloud-config\n")
        with pytest.raises(SystemExit, match="mutually exclusive"):
            load_cloud_init_user_data(ci, cmd="echo hi")

    def test_payload_over_limit_raises(self, tmp_path):
        # Write something bigger than 32 KiB.
        ci = tmp_path / "big.yaml"
        ci.write_text("x" * (USER_DATA_LIMIT_BYTES + 100))
        with pytest.raises(SystemExit, match="exceeds 32 KiB"):
            load_cloud_init_user_data(ci)

    def test_payload_over_limit_suggests_gzip(self, tmp_path):
        ci = tmp_path / "big.yaml"
        ci.write_text("x" * (USER_DATA_LIMIT_BYTES + 100))
        with pytest.raises(SystemExit) as exc:
            load_cloud_init_user_data(ci, gzip_compress=False)
        assert "--gzip" in str(exc.value)

    def test_payload_just_at_limit_ok(self, tmp_path):
        # Boundary: a payload of exactly 32 KiB is allowed.
        ci = tmp_path / "atlimit.yaml"
        ci.write_text("x" * USER_DATA_LIMIT_BYTES)
        payload = load_cloud_init_user_data(ci)
        assert payload is not None
        assert payload.payload_size == USER_DATA_LIMIT_BYTES


class TestLoadCloudInitFromCmd:
    def test_command_stdout_used_as_user_data(self):
        payload = load_cloud_init_user_data(None, cmd="echo '#cloud-config'")
        assert payload is not None
        assert "#cloud-config" in payload.user_data

    def test_command_nonzero_exit_raises(self):
        with pytest.raises(SystemExit, match="failed"):
            load_cloud_init_user_data(None, cmd="exit 7")

    def test_empty_command_output_raises(self):
        # ``true`` exits 0 with no output; the loader must reject this
        # rather than send an empty payload to Hetzner.
        with pytest.raises(SystemExit, match="no output"):
            load_cloud_init_user_data(None, cmd="true")


class TestLoadCloudInitGzip:
    def test_gzip_compresses_payload(self, tmp_path):
        ci = tmp_path / "cloud-init.yaml"
        # Use repeating content so gzip ratio is clearly > 1.
        ci.write_text("#cloud-config\n" + ("# padding line\n" * 200))
        payload = load_cloud_init_user_data(ci, gzip_compress=True)
        assert payload is not None
        assert payload.gzipped is True
        assert payload.raw_size > payload.payload_size

    def test_gzip_payload_decodes_to_original(self, tmp_path):
        ci = tmp_path / "cloud-init.yaml"
        original = "#cloud-config\npackage_update: true\n" + ("# pad\n" * 100)
        ci.write_text(original)
        payload = load_cloud_init_user_data(ci, gzip_compress=True)
        assert payload is not None
        # The user_data field is latin-1 decoded gzip bytes — round-trip them.
        recovered = gzip.decompress(payload.user_data.encode("latin-1")).decode("utf-8")
        assert recovered == original

    def test_gzip_over_limit_message_omits_gzip_hint(self, tmp_path):
        # Already gzipped and still too big — the hint about "try --gzip"
        # would be misleading. The error must say "even after gzip".
        # Base64-encoded random bytes are high-entropy text that gzip
        # cannot compress meaningfully; 256 KiB of it survives gzip well
        # over the 32 KiB ceiling.
        import base64
        import secrets

        ci = tmp_path / "huge.yaml"
        ci.write_text(base64.b64encode(secrets.token_bytes(256 * 1024)).decode("ascii"))
        with pytest.raises(SystemExit) as exc:
            load_cloud_init_user_data(ci, gzip_compress=True)
        msg = str(exc.value)
        assert "even after gzip" in msg


class TestCloudInitPayloadRatio:
    def test_ratio_when_compressed(self):
        payload = CloudInitPayload(user_data="x", gzipped=True, raw_size=1000, payload_size=200)
        assert payload.ratio == 5.0

    def test_ratio_when_uncompressed(self):
        payload = CloudInitPayload(user_data="x", gzipped=False, raw_size=500, payload_size=500)
        assert payload.ratio == 1.0

    def test_ratio_zero_payload_size_avoids_div_by_zero(self):
        # Defensive: a degenerate payload should not crash; expose 0.0 instead.
        payload = CloudInitPayload(user_data="", gzipped=False, raw_size=10, payload_size=0)
        assert payload.ratio == 0.0
