"""Shared fixtures for ots_shared tests.

Provides reusable fixtures for SSH-related tests so individual test
files don't need to repeat paramiko import guards and mock wiring.
"""

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def otsinfra_env_file(tmp_path):
    """Factory fixture that creates a .otsinfra.env file in tmp_path.

    Returns a callable: ``make(content)`` writes the content and returns
    the Path to the file.

    Usage::

        def test_something(otsinfra_env_file):
            env_file = otsinfra_env_file("OTS_HOST=eu1.example.com\\nOTS_TAG=v0.24\\n")
            assert env_file.exists()
    """

    def _make(content: str = "OTS_HOST=test.example.com\nOTS_TAG=v0.24\n"):
        path = tmp_path / ".otsinfra.env"
        path.write_text(content)
        return path

    return _make


@pytest.fixture
def mock_ssh_client():
    """A MagicMock of paramiko.SSHClient.

    Skips the test if paramiko is not installed.  The mock is created
    with ``spec=paramiko.SSHClient`` so attribute access is validated.
    """
    try:
        import paramiko
    except ImportError:
        pytest.skip("paramiko not installed")

    return MagicMock(spec=paramiko.SSHClient)


@pytest.fixture
def mock_paramiko_connect(tmp_path):
    """Patch paramiko module used by ssh_connect with pre-wired mocks.

    Creates a temporary ~/.ssh directory with empty known_hosts and config
    files so the patched ssh_connect can run without real SSH infrastructure.

    Returns a dict with keys:
        - ``client``: the mock SSHClient instance
        - ``paramiko``: the patched paramiko module mock
        - ``ssh_config``: the mock SSHConfig (set host_config via ``.lookup.return_value``)
        - ``config_path``: Path to the temporary SSH config file

    Usage::

        def test_connect(mock_paramiko_connect):
            ctx = mock_paramiko_connect
            ctx["ssh_config"].lookup.return_value = {"hostname": "10.0.0.1"}
            ssh_connect("myhost", ssh_config_path=ctx["config_path"])
            ctx["client"].connect.assert_called_once()
    """
    try:
        import paramiko
    except ImportError:
        pytest.skip("paramiko not installed")

    from unittest.mock import patch

    ssh_dir = tmp_path / ".ssh"
    ssh_dir.mkdir()
    (ssh_dir / "known_hosts").write_text("")
    config_path = ssh_dir / "config"
    config_path.write_text("")

    mock_client = MagicMock(spec=paramiko.SSHClient)
    mock_ssh_config = MagicMock()
    mock_ssh_config.lookup.return_value = {}

    with (
        patch("ots_shared.ssh.connection.paramiko") as mock_paramiko_mod,
        patch("pathlib.Path.home", return_value=tmp_path),
    ):
        mock_paramiko_mod.SSHClient.return_value = mock_client
        mock_paramiko_mod.RejectPolicy.return_value = paramiko.RejectPolicy()
        mock_paramiko_mod.SSHConfig.return_value = mock_ssh_config
        mock_paramiko_mod.ProxyCommand = paramiko.ProxyCommand

        yield {
            "client": mock_client,
            "paramiko": mock_paramiko_mod,
            "ssh_config": mock_ssh_config,
            "config_path": config_path,
        }
