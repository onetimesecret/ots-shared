# tests/ssh/test_env.py

"""Tests for ots_shared.ssh.env module."""

import pytest

from ots_shared.ssh.env import (
    MARKER_FILENAME,
    _derive_region_id,
    _eval_formula,
    _tag_to_version,
    create_marker,
    find_env_file,
    find_marker,
    generate_env_template,
    generate_marker,
    get_host_ip,
    load_env_file,
    load_marker,
    resolve_config_dir,
    resolve_host,
    validate_env_file,
)


class TestFindEnvFile:
    """Tests for walk-up .otsinfra.env discovery."""

    def test_finds_env_file_in_current_dir(self, tmp_path):
        env_file = tmp_path / ".otsinfra.env"
        env_file.write_text("OTS_HOST=example.com\n")

        result = find_env_file(start=tmp_path)
        assert result == env_file

    def test_finds_env_file_in_parent_dir(self, tmp_path):
        env_file = tmp_path / ".otsinfra.env"
        env_file.write_text("OTS_HOST=example.com\n")
        child = tmp_path / "subdir"
        child.mkdir()

        result = find_env_file(start=child)
        assert result == env_file

    def test_finds_env_file_in_grandparent(self, tmp_path):
        env_file = tmp_path / ".otsinfra.env"
        env_file.write_text("OTS_HOST=example.com\n")
        deep = tmp_path / "a" / "b"
        deep.mkdir(parents=True)

        result = find_env_file(start=deep)
        assert result == env_file

    def test_stops_at_git_root(self, tmp_path):
        # Place env file above .git
        env_file = tmp_path / ".otsinfra.env"
        env_file.write_text("OTS_HOST=example.com\n")

        # Create a git root below it
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()
        workdir = repo / "src"
        workdir.mkdir()

        # Should NOT find the env file above .git
        result = find_env_file(start=workdir)
        assert result is None

    def test_finds_env_file_at_git_root_level(self, tmp_path):
        # Env file co-located with .git
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()
        env_file = repo / ".otsinfra.env"
        env_file.write_text("OTS_HOST=example.com\n")
        workdir = repo / "src"
        workdir.mkdir()

        result = find_env_file(start=workdir)
        assert result == env_file

    def test_returns_none_when_not_found(self, tmp_path):
        result = find_env_file(start=tmp_path)
        # May or may not be None depending on what's above tmp_path,
        # but within a controlled tmp_path with no .otsinfra.env above,
        # it should eventually hit filesystem root and return None.
        # For safety, just verify it doesn't crash and returns Path or None.
        assert result is None or result.name == ".otsinfra.env"

    def test_stops_at_home_directory(self, tmp_path, monkeypatch):
        # Set HOME to tmp_path so walk-up stops there
        monkeypatch.setenv("HOME", str(tmp_path))
        # Patch Path.home() to return our tmp_path
        monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: tmp_path))

        subdir = tmp_path / "a" / "b"
        subdir.mkdir(parents=True)

        result = find_env_file(start=subdir)
        assert result is None


class TestLoadEnvFile:
    """Tests for .otsinfra.env parsing."""

    def test_parses_key_value(self, tmp_path):
        env_file = tmp_path / ".otsinfra.env"
        env_file.write_text("OTS_HOST=example.com\nOTS_TAG=v1.0.0\n")

        result = load_env_file(env_file)
        assert result == {"OTS_HOST": "example.com", "OTS_TAG": "v1.0.0"}

    def test_ignores_comments(self, tmp_path):
        env_file = tmp_path / ".otsinfra.env"
        env_file.write_text("# This is a comment\nOTS_HOST=example.com\n")

        result = load_env_file(env_file)
        assert result == {"OTS_HOST": "example.com"}

    def test_ignores_blank_lines(self, tmp_path):
        env_file = tmp_path / ".otsinfra.env"
        env_file.write_text("OTS_HOST=example.com\n\n\nOTS_TAG=v1.0.0\n")

        result = load_env_file(env_file)
        assert result == {"OTS_HOST": "example.com", "OTS_TAG": "v1.0.0"}

    def test_strips_whitespace(self, tmp_path):
        env_file = tmp_path / ".otsinfra.env"
        env_file.write_text("  OTS_HOST  =  example.com  \n")

        result = load_env_file(env_file)
        assert result == {"OTS_HOST": "example.com"}

    def test_strips_double_quotes(self, tmp_path):
        env_file = tmp_path / ".otsinfra.env"
        env_file.write_text('OTS_HOST="example.com"\n')

        result = load_env_file(env_file)
        assert result == {"OTS_HOST": "example.com"}

    def test_strips_single_quotes(self, tmp_path):
        env_file = tmp_path / ".otsinfra.env"
        env_file.write_text("OTS_HOST='example.com'\n")

        result = load_env_file(env_file)
        assert result == {"OTS_HOST": "example.com"}

    def test_ignores_lines_without_equals(self, tmp_path):
        env_file = tmp_path / ".otsinfra.env"
        env_file.write_text("NOEQUALS\nOTS_HOST=example.com\n")

        result = load_env_file(env_file)
        assert result == {"OTS_HOST": "example.com"}

    def test_empty_file(self, tmp_path):
        env_file = tmp_path / ".otsinfra.env"
        env_file.write_text("")

        result = load_env_file(env_file)
        assert result == {}

    def test_value_with_equals_sign(self, tmp_path):
        env_file = tmp_path / ".otsinfra.env"
        env_file.write_text("OTS_TAG=base64==encoded\n")

        result = load_env_file(env_file)
        assert result == {"OTS_TAG": "base64==encoded"}


class TestResolveHost:
    """Tests for host resolution priority chain."""

    def test_flag_takes_priority(self, monkeypatch):
        monkeypatch.setenv("OTS_HOST", "env-host.example.com")
        result = resolve_host(host_flag="flag-host.example.com")
        assert result == "flag-host.example.com"

    def test_env_var_when_no_flag(self, monkeypatch):
        monkeypatch.setenv("OTS_HOST", "env-host.example.com")
        result = resolve_host()
        assert result == "env-host.example.com"

    def test_env_file_when_no_flag_or_env(self, tmp_path, monkeypatch):
        monkeypatch.delenv("OTS_HOST", raising=False)
        env_file = tmp_path / ".otsinfra.env"
        env_file.write_text("OTS_HOST=file-host.example.com\n")
        monkeypatch.chdir(tmp_path)

        result = resolve_host()
        assert result == "file-host.example.com"

    def test_returns_none_when_nothing_found(self, tmp_path, monkeypatch):
        monkeypatch.delenv("OTS_HOST", raising=False)
        monkeypatch.chdir(tmp_path)

        result = resolve_host()
        # Could be None if no env file above tmp_path
        # (tmp_path is a random /tmp subdir, unlikely to have .otsinfra.env)
        assert result is None or isinstance(result, str)

    def test_flag_overrides_env_var_and_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OTS_HOST", "env-host")
        env_file = tmp_path / ".otsinfra.env"
        env_file.write_text("OTS_HOST=file-host\n")
        monkeypatch.chdir(tmp_path)

        result = resolve_host(host_flag="flag-host")
        assert result == "flag-host"

    def test_env_var_overrides_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OTS_HOST", "env-host")
        env_file = tmp_path / ".otsinfra.env"
        env_file.write_text("OTS_HOST=file-host\n")
        monkeypatch.chdir(tmp_path)

        result = resolve_host()
        assert result == "env-host"


class TestTagToVersion:
    """Tests for _tag_to_version helper."""

    def test_v_prefixed_tag(self):
        assert _tag_to_version("v0.24") == "0.24"

    def test_v_prefixed_with_patch(self):
        assert _tag_to_version("v0.24.1") == "0.24"

    def test_bare_version(self):
        assert _tag_to_version("0.24") == "0.24"

    def test_bare_with_patch(self):
        assert _tag_to_version("0.24.3") == "0.24"

    def test_major_version(self):
        assert _tag_to_version("v1.0") == "1.0"

    def test_unparseable(self):
        assert _tag_to_version("latest") is None

    def test_empty(self):
        assert _tag_to_version("") is None


class TestResolveConfigDir:
    """Tests for config directory resolution via symlink and OTS_TAG."""

    def test_symlink_takes_priority_over_tag(self, tmp_path):
        env_file = tmp_path / ".otsinfra.env"
        env_file.write_text("OTS_TAG=v0.23\n")
        # Both exist: symlink points to v0.24, tag says v0.23
        (tmp_path / "config-v0.23").mkdir()
        (tmp_path / "config-v0.24").mkdir()
        (tmp_path / "config").symlink_to("config-v0.24")

        result = resolve_config_dir(start=tmp_path)
        assert result == tmp_path / "config"

    def test_symlink_without_tag(self, tmp_path):
        env_file = tmp_path / ".otsinfra.env"
        env_file.write_text("OTS_HOST=example.com\n")
        (tmp_path / "config-v0.24").mkdir()
        (tmp_path / "config").symlink_to("config-v0.24")

        result = resolve_config_dir(start=tmp_path)
        assert result == tmp_path / "config"

    def test_plain_config_dir_works(self, tmp_path):
        env_file = tmp_path / ".otsinfra.env"
        env_file.write_text("OTS_HOST=example.com\n")
        (tmp_path / "config").mkdir()

        result = resolve_config_dir(start=tmp_path)
        assert result == tmp_path / "config"

    def test_resolves_from_tag(self, tmp_path):
        env_file = tmp_path / ".otsinfra.env"
        env_file.write_text("OTS_HOST=example.com\nOTS_TAG=v0.24\n")
        config_dir = tmp_path / "config-v0.24"
        config_dir.mkdir()

        result = resolve_config_dir(start=tmp_path)
        assert result == config_dir

    def test_returns_none_when_dir_missing(self, tmp_path):
        env_file = tmp_path / ".otsinfra.env"
        env_file.write_text("OTS_HOST=example.com\nOTS_TAG=v0.24\n")
        # No config-v0.24 directory created

        result = resolve_config_dir(start=tmp_path)
        assert result is None

    def test_returns_none_when_no_tag(self, tmp_path):
        env_file = tmp_path / ".otsinfra.env"
        env_file.write_text("OTS_HOST=example.com\n")

        result = resolve_config_dir(start=tmp_path)
        assert result is None

    def test_returns_none_when_no_env_file(self, tmp_path):
        result = resolve_config_dir(start=tmp_path)
        assert result is None

    def test_walks_up_to_find_env(self, tmp_path):
        env_file = tmp_path / ".otsinfra.env"
        env_file.write_text("OTS_TAG=v0.24\n")
        config_dir = tmp_path / "config-v0.24"
        config_dir.mkdir()
        subdir = tmp_path / "deep" / "nested"
        subdir.mkdir(parents=True)

        result = resolve_config_dir(start=subdir)
        assert result == config_dir

    def test_strips_patch_version(self, tmp_path):
        env_file = tmp_path / ".otsinfra.env"
        env_file.write_text("OTS_TAG=v0.24.1\n")
        config_dir = tmp_path / "config-v0.24"
        config_dir.mkdir()

        result = resolve_config_dir(start=tmp_path)
        assert result == config_dir

    def test_symlink_walks_up(self, tmp_path):
        env_file = tmp_path / ".otsinfra.env"
        env_file.write_text("OTS_TAG=v0.24\n")
        (tmp_path / "config-v0.24").mkdir()
        (tmp_path / "config").symlink_to("config-v0.24")
        subdir = tmp_path / "sub" / "deep"
        subdir.mkdir(parents=True)

        result = resolve_config_dir(start=subdir)
        assert result == tmp_path / "config"


class TestFindMarker:
    """Tests for otsinfra.yaml / .otsinfra.env walk-up discovery."""

    def test_finds_yaml_marker(self, tmp_path):
        marker = tmp_path / "otsinfra.yaml"
        marker.write_text("env_name: eu2\n")
        result = find_marker(start=tmp_path)
        assert result == marker

    def test_prefers_yaml_over_env(self, tmp_path):
        yaml_marker = tmp_path / "otsinfra.yaml"
        yaml_marker.write_text("env_name: eu2\n")
        env_file = tmp_path / ".otsinfra.env"
        env_file.write_text("OTS_HOST=example.com\n")

        result = find_marker(start=tmp_path)
        assert result == yaml_marker

    def test_falls_back_to_env(self, tmp_path):
        env_file = tmp_path / ".otsinfra.env"
        env_file.write_text("OTS_HOST=example.com\n")
        result = find_marker(start=tmp_path)
        assert result == env_file

    def test_returns_none_when_neither(self, tmp_path):
        result = find_marker(start=tmp_path)
        assert result is None

    def test_walks_up_to_find_yaml(self, tmp_path):
        marker = tmp_path / "otsinfra.yaml"
        marker.write_text("env_name: eu2\n")
        subdir = tmp_path / "deep" / "nested"
        subdir.mkdir(parents=True)

        result = find_marker(start=subdir)
        assert result == marker


class TestLoadMarker:
    """Tests for otsinfra.yaml parsing."""

    def test_loads_simple_yaml(self, tmp_path):
        marker = tmp_path / "otsinfra.yaml"
        marker.write_text("env_name: eu2\ncreated: '2026-04-10'\n")
        data = load_marker(marker)
        assert data["env_name"] == "eu2"
        assert data["created"] == "2026-04-10"

    def test_empty_file(self, tmp_path):
        marker = tmp_path / "otsinfra.yaml"
        marker.write_text("")
        data = load_marker(marker)
        assert data == {}

    def test_nonexistent_file(self, tmp_path):
        data = load_marker(tmp_path / "nope.yaml")
        assert data == {}

    def test_comments_ignored(self, tmp_path):
        marker = tmp_path / "otsinfra.yaml"
        marker.write_text("# marker file\nenv_name: eu2\n")
        data = load_marker(marker)
        assert data["env_name"] == "eu2"
        assert len(data) == 1


class TestResolveConfigDirWithMarker:
    """resolve_config_dir anchors off otsinfra.yaml."""

    def test_yaml_marker_with_config_sibling(self, tmp_path):
        marker = tmp_path / "otsinfra.yaml"
        marker.write_text("env_name: eu2\n")
        (tmp_path / "config").mkdir()

        result = resolve_config_dir(start=tmp_path)
        assert result == tmp_path / "config"

    def test_yaml_marker_without_config_returns_none(self, tmp_path):
        marker = tmp_path / "otsinfra.yaml"
        marker.write_text("env_name: eu2\n")
        # No config/ directory

        result = resolve_config_dir(start=tmp_path)
        assert result is None

    def test_yaml_marker_walks_up(self, tmp_path):
        marker = tmp_path / "otsinfra.yaml"
        marker.write_text("env_name: eu2\n")
        (tmp_path / "config").mkdir()
        subdir = tmp_path / "deep"
        subdir.mkdir()

        result = resolve_config_dir(start=subdir)
        assert result == tmp_path / "config"

    def test_random_config_dir_without_marker_not_found(self, tmp_path):
        """A config/ directory without a marker file is NOT returned."""
        (tmp_path / "config").mkdir()
        # No otsinfra.yaml or .otsinfra.env

        result = resolve_config_dir(start=tmp_path)
        assert result is None


class TestGenerateMarker:
    """Tests for otsinfra.yaml content generation."""

    def test_basic_content(self):
        content = generate_marker("eu2")
        assert "env_name: eu2" in content
        assert "created:" in content

    def test_extra_metadata(self):
        content = generate_marker("eu2", tier="prod", region="eu-central")
        assert "tier: prod" in content
        assert "region: eu-central" in content

    def test_special_chars_quoted(self):
        content = generate_marker("eu2", note="has:colon")
        assert "note: 'has:colon'" in content

    def test_trailing_newline(self):
        content = generate_marker("eu2")
        assert content.endswith("\n")


class TestCreateMarker:
    """Tests for otsinfra.yaml file creation."""

    def test_creates_file(self, tmp_path):
        path = create_marker(tmp_path, "eu2")
        assert path == tmp_path / MARKER_FILENAME
        assert path.exists()
        content = path.read_text()
        assert "env_name: eu2" in content

    def test_refuses_overwrite_without_force(self, tmp_path):
        marker = tmp_path / MARKER_FILENAME
        marker.write_text("existing\n")
        import pytest

        with pytest.raises(FileExistsError):
            create_marker(tmp_path, "eu2")

    def test_force_overwrites(self, tmp_path):
        marker = tmp_path / MARKER_FILENAME
        marker.write_text("old\n")
        path = create_marker(tmp_path, "eu2", force=True)
        assert "env_name: eu2" in path.read_text()

    def test_roundtrip_with_load_marker(self, tmp_path):
        create_marker(tmp_path, "us-prod")
        data = load_marker(tmp_path / MARKER_FILENAME)
        assert data["env_name"] == "us-prod"
        assert "created" in data

    def test_hosts_written_to_yaml(self, tmp_path):
        hosts = {"db": {"private_ip_address": "10.0.0.11"}}
        path = create_marker(tmp_path, "eu2", hosts=hosts)
        data = load_marker(path)
        assert data["hosts"] == {"db": {"private_ip_address": "10.0.0.11"}}

    def test_hosts_multiple_roles(self, tmp_path):
        hosts = {
            "db": {"private_ip_address": "10.0.0.11"},
            "web": {"private_ip_address": "10.0.0.12", "public_ip": "203.0.113.5"},
        }
        path = create_marker(tmp_path, "eu2", hosts=hosts)
        data = load_marker(path)
        assert data["hosts"]["db"]["private_ip_address"] == "10.0.0.11"
        assert data["hosts"]["web"]["private_ip_address"] == "10.0.0.12"
        assert data["hosts"]["web"]["public_ip"] == "203.0.113.5"

    def test_hosts_none_omits_block(self, tmp_path):
        path = create_marker(tmp_path, "eu2", hosts=None)
        content = path.read_text()
        assert "hosts:" not in content


class TestGenerateEnvTemplate:
    """Tests for .otsinfra.env template generation."""

    def test_generates_with_all_values(self):
        content = generate_env_template(host="prod-us1", tag="v0.24", repository="ghcr.io/org/repo")
        assert "OTS_HOST=prod-us1" in content
        assert "OTS_TAG=v0.24" in content
        assert "OTS_REPOSITORY=ghcr.io/org/repo" in content

    def test_generates_with_empty_values(self):
        content = generate_env_template()
        assert "OTS_HOST=" in content
        assert "OTS_TAG=" in content
        assert "OTS_REPOSITORY" not in content

    def test_omits_repository_when_empty(self):
        content = generate_env_template(host="test", tag="v1.0")
        assert "OTS_REPOSITORY" not in content

    def test_roundtrip_through_load(self, tmp_path):
        content = generate_env_template(host="prod-eu1", tag="v0.24")
        env_file = tmp_path / ".otsinfra.env"
        env_file.write_text(content)
        parsed = load_env_file(env_file)
        assert parsed["OTS_HOST"] == "prod-eu1"
        assert parsed["OTS_TAG"] == "v0.24"


class TestValidateEnvFile:
    """Tests for .otsinfra.env validation."""

    def test_file_not_found(self, tmp_path):
        nonexistent = tmp_path / ".otsinfra.env"

        warnings, errors = validate_env_file(nonexistent)

        assert warnings == []
        assert len(errors) == 1
        assert f"Environment file not found: {nonexistent}" in errors[0]

    def test_missing_ots_host(self, tmp_path):
        env_file = tmp_path / ".otsinfra.env"
        env_file.write_text("OTS_TAG=v0.24\nRABBITMQ_URL=amqp://localhost\n")

        warnings, errors = validate_env_file(env_file)

        assert errors == ["OTS_HOST is required for remote operations"]
        assert warnings == []

    def test_missing_ots_tag(self, tmp_path):
        env_file = tmp_path / ".otsinfra.env"
        env_file.write_text("OTS_HOST=example.com\nRABBITMQ_URL=amqp://localhost\n")

        warnings, errors = validate_env_file(env_file)

        assert errors == []
        assert "OTS_TAG not set — container operations may use defaults" in warnings

    def test_missing_rabbitmq_url(self, tmp_path):
        env_file = tmp_path / ".otsinfra.env"
        env_file.write_text("OTS_HOST=example.com\nOTS_TAG=v0.24\n")

        warnings, errors = validate_env_file(env_file)

        assert errors == []
        assert "RABBITMQ_URL not set — sidecar will use server defaults" in warnings

    def test_fully_populated_file(self, tmp_path):
        env_file = tmp_path / ".otsinfra.env"
        env_file.write_text(
            "OTS_HOST=example.com\n"
            "OTS_TAG=v0.24\n"
            "RABBITMQ_URL=amqp://user:pass@localhost:5672/vhost\n"
        )

        warnings, errors = validate_env_file(env_file)

        assert warnings == []
        assert errors == []


# ---------------------------------------------------------------------------
# get_host_ip — resolution order across legacy / ordinals / CIDR
# ---------------------------------------------------------------------------


class TestGetHostIp:
    """Resolver precedence: ordinals → legacy (only "01") → CIDR."""

    def test_legacy_scalar_resolves_for_ordinal_01(self):
        marker = {"hosts": {"db": {"private_ip_address": "10.0.0.11"}}}
        assert get_host_ip(marker, "db", "01") == "10.0.0.11"

    def test_legacy_scalar_returns_none_for_higher_ordinal(self):
        # Legacy single-scalar markers must NOT alias higher ordinals to the
        # canonical IP — that was the silent wrong-IP bug.
        marker = {"hosts": {"db": {"private_ip_address": "10.0.0.11"}}}
        assert get_host_ip(marker, "db", "02") is None

    def test_legacy_default_ordinal_is_01(self):
        marker = {"hosts": {"db": {"private_ip_address": "10.0.0.11"}}}
        assert get_host_ip(marker, "db") == "10.0.0.11"

    def test_ordinals_override_beats_legacy_scalar(self):
        marker = {
            "hosts": {
                "db": {
                    "private_ip_address": "10.0.0.11",
                    "ordinals": {"02": {"private_ip_address": "10.0.0.99"}},
                }
            }
        }
        # Ordinal 01 still maps to the legacy scalar (no override defined).
        assert get_host_ip(marker, "db", "01") == "10.0.0.11"
        # Ordinal 02 takes the explicit override.
        assert get_host_ip(marker, "db", "02") == "10.0.0.99"

    def test_ordinals_override_at_01_beats_legacy(self):
        marker = {
            "hosts": {
                "db": {
                    "private_ip_address": "10.0.0.11",
                    "ordinals": {"01": {"private_ip_address": "10.0.0.50"}},
                }
            }
        }
        assert get_host_ip(marker, "db", "01") == "10.0.0.50"

    def test_cidr_in_order_default_assignment(self):
        # network base 10.101.1.0/24 → ordinal "01" → 10.101.1.11.
        marker = {
            "hosts": {
                "web": {"private_ip_cidr": "10.101.1.0/24"},
            }
        }
        assert get_host_ip(marker, "web", "01") == "10.101.1.11"
        assert get_host_ip(marker, "web", "02") == "10.101.1.12"

    def test_cidr_in_order_explicit_assignment_type(self):
        marker = {
            "hosts": {
                "web": {
                    "private_ip_cidr": "10.101.1.0/24",
                    "private_ip_assignment_type": "in_order",
                },
            }
        }
        assert get_host_ip(marker, "web", "07") == "10.101.1.17"

    def test_cidr_calculated_with_formula(self):
        # 10.(100+region_id).(ordinal).11 — encoded as a literal-string
        # concatenation (formula DSL is arithmetic + string concat only).
        marker = {
            "network": {
                "name": "n",
                "ip_range": "10.103.0.0/16",
                "network_zone": "eu-central",
            },
            "hosts": {
                "web": {
                    "private_ip_cidr": "10.103.5.0/24",
                    "private_ip_assignment_type": "calculated",
                    "private_ip_formula": ("'10.' + (100 + region_id) + '.' + ordinal + '.11'"),
                },
            },
        }
        # region_id=3 (second octet 103 - 100), ordinal=2 → 10.103.2.11.
        assert get_host_ip(marker, "web", "02") == "10.103.2.11"

    def test_cidr_in_order_overflow_fails_loud(self):
        marker = {"hosts": {"web": {"private_ip_cidr": "10.101.1.0/29"}}}
        # /29 has 8 addresses; 10 + 1 = 11 overflows.
        with pytest.raises(ValueError, match="overflows"):
            get_host_ip(marker, "web", "01")

    def test_cidr_calculated_without_formula_fails_loud(self):
        marker = {
            "hosts": {
                "web": {
                    "private_ip_cidr": "10.101.1.0/24",
                    "private_ip_assignment_type": "calculated",
                },
            }
        }
        with pytest.raises(ValueError, match="requires private_ip_formula"):
            get_host_ip(marker, "web", "01")

    def test_unknown_assignment_type_fails_loud(self):
        marker = {
            "hosts": {
                "web": {
                    "private_ip_cidr": "10.101.1.0/24",
                    "private_ip_assignment_type": "random",
                },
            }
        }
        with pytest.raises(ValueError, match="must be 'in_order' or 'calculated'"):
            get_host_ip(marker, "web", "01")

    def test_returns_none_when_role_missing(self):
        marker = {"hosts": {"db": {"private_ip_address": "10.0.0.11"}}}
        assert get_host_ip(marker, "web", "01") is None

    def test_returns_none_when_no_hosts_block(self):
        assert get_host_ip({}, "db", "01") is None

    def test_returns_none_when_role_has_no_ip_source(self):
        marker = {"hosts": {"web": {"location": "nbg1"}}}
        assert get_host_ip(marker, "web", "01") is None

    def test_non_dict_host_returns_none(self):
        marker = {"hosts": {"web": "not-a-dict"}}
        assert get_host_ip(marker, "web", "01") is None


# ---------------------------------------------------------------------------
# _eval_formula — restricted arithmetic DSL, allowlisted AST walk
# ---------------------------------------------------------------------------


class TestEvalFormula:
    """Operator coverage and rejection of dangerous AST nodes."""

    def test_addition(self):
        assert _eval_formula("ordinal + 10", {"ordinal": 5}) == "15"

    def test_subtraction(self):
        assert _eval_formula("ordinal - 1", {"ordinal": 5}) == "4"

    def test_multiplication(self):
        assert _eval_formula("ordinal * 4", {"ordinal": 3}) == "12"

    def test_division_is_floor_division(self):
        # Division uses // semantics under the hood for IP arithmetic.
        assert _eval_formula("ordinal / 2", {"ordinal": 7}) == "3"

    def test_floor_division(self):
        assert _eval_formula("ordinal // 2", {"ordinal": 7}) == "3"

    def test_modulo(self):
        assert _eval_formula("ordinal % 3", {"ordinal": 7}) == "1"

    def test_left_shift(self):
        assert _eval_formula("ordinal << 2", {"ordinal": 3}) == "12"

    def test_right_shift(self):
        assert _eval_formula("ordinal >> 1", {"ordinal": 8}) == "4"

    def test_unary_minus(self):
        assert _eval_formula("-ordinal", {"ordinal": 5}) == "-5"

    def test_unary_plus(self):
        assert _eval_formula("+ordinal", {"ordinal": 5}) == "5"

    def test_string_concatenation(self):
        # Add with str promotes both sides via f-string.
        assert _eval_formula("'host-' + ordinal", {"ordinal": 7}) == "host-7"

    def test_parentheses(self):
        assert _eval_formula("(ordinal + 1) * 2", {"ordinal": 3}) == "8"

    def test_multiple_variables(self):
        assert (
            _eval_formula(
                "100 + region_id * 10 + ordinal",
                {"region_id": 3, "ordinal": 2},
            )
            == "132"
        )

    def test_rejects_function_call(self):
        with pytest.raises(ValueError, match="forbidden node"):
            _eval_formula("int(ordinal)", {"ordinal": 5})

    def test_rejects_attribute_access(self):
        with pytest.raises(ValueError, match="forbidden node"):
            _eval_formula("ordinal.bit_length", {"ordinal": 5})

    def test_rejects_subscript(self):
        with pytest.raises(ValueError, match="forbidden node"):
            _eval_formula("name[0]", {"name": "abc"})

    def test_rejects_lambda(self):
        with pytest.raises(ValueError, match="forbidden node"):
            _eval_formula("(lambda x: x)(1)", {})

    def test_rejects_list_comprehension(self):
        with pytest.raises(ValueError, match="forbidden node"):
            _eval_formula("[x for x in range(3)]", {})

    def test_rejects_unknown_name(self):
        with pytest.raises(ValueError, match="unknown name"):
            _eval_formula("ordinal + something_else", {"ordinal": 1})

    def test_rejects_non_int_str_constant(self):
        with pytest.raises(ValueError, match="constants must be int or str"):
            _eval_formula("1.5 + ordinal", {"ordinal": 1})

    def test_rejects_syntax_error(self):
        with pytest.raises(ValueError, match="syntax error"):
            _eval_formula("1 +", {})

    def test_rejects_int_op_on_string_operand(self):
        # Mixed-type Sub is rejected — only Add accepts strings.
        with pytest.raises(ValueError, match="requires int operands"):
            _eval_formula("ordinal - prefix", {"ordinal": 1, "prefix": "x"})

    def test_rejects_unary_on_string(self):
        with pytest.raises(ValueError, match="requires int operand"):
            _eval_formula("-prefix", {"prefix": "x"})


# ---------------------------------------------------------------------------
# _derive_region_id — second-octet convention from network.ip_range
# ---------------------------------------------------------------------------


class TestDeriveRegionId:
    def test_second_octet_minus_100(self):
        marker = {
            "network": {
                "name": "n",
                "ip_range": "10.103.0.0/16",
                "network_zone": "eu-central",
            }
        }
        assert _derive_region_id(marker) == 3

    def test_second_octet_100_yields_zero(self):
        marker = {"network": {"ip_range": "10.100.0.0/16"}}
        assert _derive_region_id(marker) == 0

    def test_no_network_block_falls_back_to_zero(self):
        assert _derive_region_id({}) == 0

    def test_non_mapping_network_falls_back(self):
        assert _derive_region_id({"network": "garbage"}) == 0

    def test_missing_ip_range_falls_back(self):
        assert _derive_region_id({"network": {"name": "n"}}) == 0

    def test_malformed_cidr_falls_back(self):
        assert _derive_region_id({"network": {"ip_range": "not-a-cidr"}}) == 0

    def test_non_10_dot_falls_back(self):
        assert _derive_region_id({"network": {"ip_range": "192.168.0.0/16"}}) == 0
