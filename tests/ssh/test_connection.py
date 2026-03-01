"""Tests for ots_shared.ssh.connection module."""

from unittest.mock import MagicMock, patch

import pytest

try:
    import paramiko
except ImportError:
    pytest.skip("paramiko not installed", allow_module_level=True)

from ots_shared.ssh.connection import ssh_connect


class TestSSHConnect:
    """Tests for the ssh_connect factory function."""

    def test_creates_client_with_reject_policy(self, tmp_path):
        mock_client = MagicMock(spec=paramiko.SSHClient)

        ssh_dir = tmp_path / ".ssh"
        ssh_dir.mkdir()
        (ssh_dir / "known_hosts").write_text("")
        config = ssh_dir / "config"
        config.write_text("")

        with (
            patch("ots_shared.ssh.connection.paramiko") as mock_paramiko,
            patch("pathlib.Path.home", return_value=tmp_path),
        ):
            mock_paramiko.SSHClient.return_value = mock_client
            mock_paramiko.RejectPolicy.return_value = paramiko.RejectPolicy()
            mock_ssh_config = MagicMock()
            mock_ssh_config.lookup.return_value = {}
            mock_paramiko.SSHConfig.return_value = mock_ssh_config

            ssh_connect("example.com", ssh_config_path=config)

        mock_client.set_missing_host_key_policy.assert_called_once()
        mock_client.connect.assert_called_once()

    def test_uses_ssh_config_for_user_and_port(self, tmp_path):
        mock_client = MagicMock(spec=paramiko.SSHClient)
        host_config = {
            "hostname": "10.0.0.1",
            "user": "deploy",
            "port": "2222",
        }

        ssh_dir = tmp_path / ".ssh"
        ssh_dir.mkdir()
        (ssh_dir / "known_hosts").write_text("")
        config = ssh_dir / "config"
        config.write_text("")

        with (
            patch("ots_shared.ssh.connection.paramiko") as mock_paramiko,
            patch("pathlib.Path.home", return_value=tmp_path),
        ):
            mock_paramiko.SSHClient.return_value = mock_client
            mock_paramiko.RejectPolicy.return_value = paramiko.RejectPolicy()
            mock_ssh_config = MagicMock()
            mock_ssh_config.lookup.return_value = host_config
            mock_paramiko.SSHConfig.return_value = mock_ssh_config

            ssh_connect("myserver", ssh_config_path=config)

        connect_kwargs = mock_client.connect.call_args.kwargs
        assert connect_kwargs["hostname"] == "10.0.0.1"
        assert connect_kwargs["port"] == 2222
        assert connect_kwargs["username"] == "deploy"

    def test_uses_identity_file(self, tmp_path):
        mock_client = MagicMock(spec=paramiko.SSHClient)
        key_file = tmp_path / ".ssh" / "id_ed25519"
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key_file.write_text("fake key")
        (tmp_path / ".ssh" / "known_hosts").write_text("")
        config = tmp_path / ".ssh" / "config"
        config.write_text("")

        host_config = {
            "hostname": "example.com",
            "identityfile": [str(key_file)],
        }

        with (
            patch("ots_shared.ssh.connection.paramiko") as mock_paramiko,
            patch("pathlib.Path.home", return_value=tmp_path),
        ):
            mock_paramiko.SSHClient.return_value = mock_client
            mock_paramiko.RejectPolicy.return_value = paramiko.RejectPolicy()
            mock_ssh_config = MagicMock()
            mock_ssh_config.lookup.return_value = host_config
            mock_paramiko.SSHConfig.return_value = mock_ssh_config

            ssh_connect("example.com", ssh_config_path=config)

        connect_kwargs = mock_client.connect.call_args.kwargs
        assert connect_kwargs["key_filename"] == [str(key_file)]

    def test_proxy_command_support(self, tmp_path):
        mock_client = MagicMock(spec=paramiko.SSHClient)
        ssh_dir = tmp_path / ".ssh"
        ssh_dir.mkdir()
        (ssh_dir / "known_hosts").write_text("")
        config = ssh_dir / "config"
        config.write_text("")

        host_config = {
            "hostname": "example.com",
            "proxycommand": "ssh -W %h:%p bastion",
        }

        with (
            patch("ots_shared.ssh.connection.paramiko") as mock_paramiko,
            patch("pathlib.Path.home", return_value=tmp_path),
        ):
            mock_paramiko.SSHClient.return_value = mock_client
            mock_paramiko.RejectPolicy.return_value = paramiko.RejectPolicy()
            mock_ssh_config = MagicMock()
            mock_ssh_config.lookup.return_value = host_config
            mock_paramiko.SSHConfig.return_value = mock_ssh_config

            ssh_connect("example.com", ssh_config_path=config)

        mock_paramiko.ProxyCommand.assert_called_once_with("ssh -W %h:%p bastion")

    def test_default_timeout(self, tmp_path):
        mock_client = MagicMock(spec=paramiko.SSHClient)
        ssh_dir = tmp_path / ".ssh"
        ssh_dir.mkdir()
        (ssh_dir / "known_hosts").write_text("")
        config = ssh_dir / "config"
        config.write_text("")

        with (
            patch("ots_shared.ssh.connection.paramiko") as mock_paramiko,
            patch("pathlib.Path.home", return_value=tmp_path),
        ):
            mock_paramiko.SSHClient.return_value = mock_client
            mock_paramiko.RejectPolicy.return_value = paramiko.RejectPolicy()
            mock_ssh_config = MagicMock()
            mock_ssh_config.lookup.return_value = {}
            mock_paramiko.SSHConfig.return_value = mock_ssh_config

            ssh_connect("example.com", ssh_config_path=config)

        connect_kwargs = mock_client.connect.call_args.kwargs
        assert connect_kwargs["timeout"] == 15
