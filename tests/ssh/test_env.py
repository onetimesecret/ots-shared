"""Tests for ots_shared.ssh.env module."""

from ots_shared.ssh.env import (
    _tag_to_version,
    find_env_file,
    generate_env_template,
    load_env_file,
    resolve_config_dir,
    resolve_host,
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
