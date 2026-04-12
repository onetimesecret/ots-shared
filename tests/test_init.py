# packages/rots/packages/ots-shared/tests/test_init.py

"""Tests for ots_shared.init — shared init sub-app."""

from pathlib import Path
from unittest.mock import patch

import pytest

from ots_shared.init import app, init
from ots_shared.ssh.env import MARKER_FILENAME, load_marker


class TestInitCreatesFiles:
    """The init command should create marker, .gitignore, and .envrc."""

    def test_creates_marker_with_explicit_name(self, tmp_path):
        with pytest.raises(SystemExit) as exc_info:
            app(["myenv", "--directory", str(tmp_path)])
        assert exc_info.value.code is None or exc_info.value.code == 0
        marker = tmp_path / MARKER_FILENAME
        assert marker.exists()
        data = load_marker(marker)
        assert data["environment"] == "myenv"

    def test_defaults_name_from_directory(self, tmp_path):
        target = tmp_path / "staging"
        target.mkdir()
        with pytest.raises(SystemExit) as exc_info:
            app(["--directory", str(target)])
        assert exc_info.value.code is None or exc_info.value.code == 0
        data = load_marker(target / MARKER_FILENAME)
        assert data["environment"] == "staging"

    def test_creates_gitignore(self, tmp_path):
        with pytest.raises(SystemExit):
            app(["env1", "--directory", str(tmp_path)])
        assert (tmp_path / ".gitignore").exists()

    def test_creates_envrc(self, tmp_path):
        with pytest.raises(SystemExit):
            app(["env1", "--directory", str(tmp_path)])
        assert (tmp_path / ".envrc").exists()


class TestInitOverwriteBehavior:
    """Marker file overwrite and --force flag."""

    def test_refuses_overwrite_without_force(self, tmp_path):
        marker = tmp_path / MARKER_FILENAME
        marker.write_text("existing\n")
        with pytest.raises(SystemExit) as exc_info:
            app(["eu2", "--directory", str(tmp_path)])
        assert exc_info.value.code == 1
        assert marker.read_text() == "existing\n"

    def test_force_overwrites_marker(self, tmp_path):
        marker = tmp_path / MARKER_FILENAME
        marker.write_text("old\n")
        with pytest.raises(SystemExit):
            app(["eu2", "--directory", str(tmp_path), "--force"])
        data = load_marker(marker)
        assert data["environment"] == "eu2"

    def test_force_overwrites_gitignore(self, tmp_path):
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text("old content\n")
        with pytest.raises(SystemExit):
            app(["env1", "--directory", str(tmp_path), "--force"])
        assert gitignore.read_text() != "old content\n"

    def test_force_overwrites_envrc(self, tmp_path):
        envrc = tmp_path / ".envrc"
        envrc.write_text("old content\n")
        with pytest.raises(SystemExit):
            app(["env1", "--directory", str(tmp_path), "--force"])
        assert envrc.read_text() != "old content\n"


class TestInitErrorCases:
    """Error handling for invalid inputs."""

    def test_nonexistent_directory_exits_with_code_1(self, tmp_path):
        nonexistent = tmp_path / "does-not-exist"
        with pytest.raises(SystemExit) as exc_info:
            app(["env1", "--directory", str(nonexistent)])
        assert exc_info.value.code == 1

    def test_existing_scaffold_without_force_still_creates_marker(self, tmp_path):
        """When .gitignore/.envrc exist but marker does not, marker is created
        and warnings are emitted for the scaffold files."""
        (tmp_path / ".gitignore").write_text("old\n")
        (tmp_path / ".envrc").write_text("old\n")
        with pytest.raises(SystemExit) as exc_info:
            app(["env1", "--directory", str(tmp_path)])
        # Marker should succeed (exit 0 or None), scaffold warnings go to stderr
        assert exc_info.value.code is None or exc_info.value.code == 0
        assert (tmp_path / MARKER_FILENAME).exists()
        # Scaffold files should NOT have been overwritten
        assert (tmp_path / ".gitignore").read_text() == "old\n"
        assert (tmp_path / ".envrc").read_text() == "old\n"


class TestInitDefaultEnvironmentFallback:
    """Edge case: environment falls back to 'default' when directory name is empty."""

    @patch("ots_shared.init.create_envrc_template")
    @patch("ots_shared.init.create_gitignore")
    @patch("ots_shared.init.create_marker")
    def test_root_path_uses_default_as_environment_name(
        self, mock_create_marker, mock_create_gitignore, mock_create_envrc_template
    ):
        """Path('/').resolve().name is '', so environment should fall back to 'default'."""
        mock_create_marker.return_value = Path("/.otsinfra.yaml")
        mock_create_gitignore.return_value = Path("/.gitignore")
        mock_create_envrc_template.return_value = Path("/.envrc")

        init(directory=Path("/"))

        mock_create_marker.assert_called_once()
        call_args = mock_create_marker.call_args
        assert call_args[0][1] == "default"
