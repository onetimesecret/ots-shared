"""Tests for ots_shared.ssh.executor module."""

import os
import subprocess
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from ots_shared.ssh.executor import (
    SSH_DEFAULT_TIMEOUT,
    CommandError,
    Executor,
    LocalExecutor,
    Result,
    SSHExecutor,
    is_remote,
)


class TestResult:
    """Tests for the Result dataclass."""

    def test_ok_when_zero(self):
        r = Result(command="echo hi", returncode=0, stdout="hi\n", stderr="")
        assert r.ok is True

    def test_not_ok_when_nonzero(self):
        r = Result(command="false", returncode=1, stdout="", stderr="error")
        assert r.ok is False

    def test_check_passes_on_zero(self):
        r = Result(command="true", returncode=0, stdout="", stderr="")
        r.check()  # should not raise

    def test_check_raises_command_error_on_nonzero(self):
        r = Result(command="false", returncode=1, stdout="", stderr="oops")
        with pytest.raises(CommandError) as exc_info:
            r.check()
        assert exc_info.value.result is r
        assert "exit 1" in str(exc_info.value)

    def test_frozen(self):
        r = Result(command="ls", returncode=0, stdout="", stderr="")
        with pytest.raises(AttributeError):
            r.returncode = 1  # type: ignore[misc]


class TestCommandError:
    """Tests for CommandError."""

    def test_message_contains_command(self):
        r = Result(command="rm -rf /", returncode=1, stdout="", stderr="")
        err = CommandError(r)
        assert "rm -rf /" in str(err)
        assert "exit 1" in str(err)

    def test_result_attached(self):
        r = Result(command="fail", returncode=42, stdout="", stderr="")
        err = CommandError(r)
        assert err.result is r
        assert err.result.returncode == 42


class TestLocalExecutor:
    """Tests for LocalExecutor."""

    def test_satisfies_executor_protocol(self):
        assert isinstance(LocalExecutor(), Executor)

    @patch("subprocess.run")
    def test_run_simple_command(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=["echo", "hi"], returncode=0, stdout="hi\n", stderr=""
        )
        executor = LocalExecutor()
        result = executor.run(["echo", "hi"])

        assert result.ok
        assert result.stdout == "hi\n"
        mock_run.assert_called_once_with(
            ["echo", "hi"], capture_output=True, text=True, timeout=None
        )

    @patch("subprocess.run")
    def test_run_with_sudo(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        executor = LocalExecutor()
        executor.run(["systemctl", "start", "foo"], sudo=True)

        mock_run.assert_called_once_with(
            ["sudo", "--", "systemctl", "start", "foo"],
            capture_output=True,
            text=True,
            timeout=None,
        )

    @patch("subprocess.run")
    def test_run_with_timeout(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        executor = LocalExecutor()
        executor.run(["sleep", "1"], timeout=30)

        mock_run.assert_called_once_with(["sleep", "1"], capture_output=True, text=True, timeout=30)

    @patch("subprocess.run")
    def test_run_with_check_raises_on_failure(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="nope"
        )
        executor = LocalExecutor()
        with pytest.raises(CommandError):
            executor.run(["false"], check=True)

    @patch("subprocess.run")
    def test_run_without_check_does_not_raise(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr=""
        )
        executor = LocalExecutor()
        result = executor.run(["false"])
        assert not result.ok  # does not raise

    @patch("subprocess.run")
    def test_timeout_expired_returns_result_with_124(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="slow", timeout=5)
        executor = LocalExecutor()
        result = executor.run(["slow"], timeout=5)
        assert result.returncode == 124

    def test_close_is_noop(self):
        executor = LocalExecutor()
        executor.close()  # should not raise

    @patch("subprocess.run")
    def test_command_string_is_quoted(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        executor = LocalExecutor()
        result = executor.run(["echo", "hello world"])
        assert "hello world" in result.command  # quoted in command string

    @patch("subprocess.run")
    def test_run_with_input(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        executor = LocalExecutor()
        executor.run(["tee", "/tmp/test"], input="file content\n")

        mock_run.assert_called_once_with(
            ["tee", "/tmp/test"],
            capture_output=True,
            text=True,
            timeout=None,
            input="file content\n",
        )

    @patch("subprocess.run")
    def test_run_without_input_omits_kwarg(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        executor = LocalExecutor()
        executor.run(["echo", "hi"])

        # input=None should not appear in kwargs
        call_kwargs = mock_run.call_args[1]
        assert "input" not in call_kwargs


class TestLocalExecutorStreaming:
    """Tests for LocalExecutor.run_stream() and run_interactive()."""

    @patch("subprocess.run")
    def test_run_stream_returns_exit_code(self, mock_run):
        """run_stream should return the process exit code."""
        mock_run.return_value = subprocess.CompletedProcess(args=["echo", "hi"], returncode=0)
        executor = LocalExecutor()
        rc = executor.run_stream(["echo", "hi"])

        assert rc == 0
        # Must be called WITHOUT capture_output (output goes to terminal)
        mock_run.assert_called_once_with(["echo", "hi"], timeout=None)

    @patch("subprocess.run")
    def test_run_stream_timeout_returns_124(self, mock_run):
        """run_stream should return 124 on TimeoutExpired (matching coreutils timeout)."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="slow", timeout=10)
        executor = LocalExecutor()
        rc = executor.run_stream(["slow"], timeout=10)

        assert rc == 124

    @patch("subprocess.run")
    def test_run_stream_with_sudo(self, mock_run):
        """run_stream with sudo=True should prepend sudo --."""
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        executor = LocalExecutor()
        executor.run_stream(["journalctl", "-f"], sudo=True)

        mock_run.assert_called_once_with(["sudo", "--", "journalctl", "-f"], timeout=None)

    @patch("subprocess.run")
    def test_run_stream_passes_timeout(self, mock_run):
        """run_stream should pass timeout to subprocess.run."""
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        executor = LocalExecutor()
        executor.run_stream(["tail", "-f", "/var/log/syslog"], timeout=300)

        mock_run.assert_called_once_with(["tail", "-f", "/var/log/syslog"], timeout=300)

    @patch("subprocess.run")
    def test_run_stream_nonzero_exit(self, mock_run):
        """run_stream should return non-zero exit codes faithfully."""
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=2)
        executor = LocalExecutor()
        rc = executor.run_stream(["grep", "missing"])

        assert rc == 2

    @patch("subprocess.run")
    def test_run_interactive_returns_exit_code(self, mock_run):
        """run_interactive should return the process exit code."""
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        executor = LocalExecutor()
        rc = executor.run_interactive(["bash"])

        assert rc == 0
        # Called with no capture, no timeout — inherits terminal
        mock_run.assert_called_once_with(["bash"])

    @patch("subprocess.run")
    def test_run_interactive_with_sudo(self, mock_run):
        """run_interactive with sudo=True should prepend sudo --."""
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        executor = LocalExecutor()
        executor.run_interactive(["podman", "exec", "-it", "ctr", "/bin/sh"], sudo=True)

        mock_run.assert_called_once_with(["sudo", "--", "podman", "exec", "-it", "ctr", "/bin/sh"])

    @patch("subprocess.run")
    def test_run_interactive_nonzero_exit(self, mock_run):
        """run_interactive should return non-zero exit codes faithfully."""
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=130)
        executor = LocalExecutor()
        rc = executor.run_interactive(["bash"])

        assert rc == 130


class TestSSHExecutor:
    """Tests for SSHExecutor."""

    def _make_mock_client(self):
        """Create a mock paramiko SSHClient."""
        try:
            import paramiko
        except ImportError:
            pytest.skip("paramiko not installed")

        client = MagicMock(spec=paramiko.SSHClient)
        return client

    def _configure_exec_command(self, client, stdout_data="", stderr_data="", exit_code=0):
        """Configure mock client.exec_command to return given data."""
        mock_stdout = MagicMock()
        mock_stdout.read.return_value = stdout_data.encode()
        mock_stdout.channel.recv_exit_status.return_value = exit_code

        mock_stderr = MagicMock()
        mock_stderr.read.return_value = stderr_data.encode()

        mock_stdin = MagicMock()
        client.exec_command.return_value = (mock_stdin, mock_stdout, mock_stderr)
        return mock_stdin, mock_stdout, mock_stderr

    def test_satisfies_executor_protocol(self):
        client = self._make_mock_client()
        assert isinstance(SSHExecutor(client), Executor)

    def test_run_simple_command(self):
        client = self._make_mock_client()
        self._configure_exec_command(client, stdout_data="active\n", exit_code=0)

        executor = SSHExecutor(client)
        result = executor.run(["systemctl", "is-active", "foo"])

        assert result.ok
        assert result.stdout == "active\n"
        client.exec_command.assert_called_once_with(
            "systemctl is-active foo", timeout=SSH_DEFAULT_TIMEOUT
        )

    def test_run_with_sudo(self):
        client = self._make_mock_client()
        self._configure_exec_command(client, exit_code=0)

        executor = SSHExecutor(client)
        executor.run(["systemctl", "start", "foo"], sudo=True)

        client.exec_command.assert_called_once_with(
            "sudo -- systemctl start foo", timeout=SSH_DEFAULT_TIMEOUT
        )

    def test_arguments_are_shlex_quoted(self):
        client = self._make_mock_client()
        self._configure_exec_command(client, exit_code=0)

        executor = SSHExecutor(client)
        executor.run(["echo", "hello world", "it's"])

        # shlex.quote wraps args containing spaces/special chars in single quotes
        call_args = client.exec_command.call_args[0][0]
        assert "'hello world'" in call_args
        # shlex.quote handles apostrophes by escaping out of single quotes
        assert "it" in call_args  # the word is present, just quoted differently

    def test_run_with_timeout(self):
        client = self._make_mock_client()
        self._configure_exec_command(client, exit_code=0)

        executor = SSHExecutor(client)
        executor.run(["sleep", "1"], timeout=30)

        client.exec_command.assert_called_once_with("sleep 1", timeout=30)

    def test_run_default_timeout_applied(self):
        """When no timeout is passed, SSH_DEFAULT_TIMEOUT is used."""
        client = self._make_mock_client()
        self._configure_exec_command(client, exit_code=0)

        executor = SSHExecutor(client)
        executor.run(["echo", "hi"])

        client.exec_command.assert_called_once_with("echo hi", timeout=SSH_DEFAULT_TIMEOUT)

    def test_run_explicit_none_timeout_disables_default(self):
        """Passing timeout=None explicitly should disable the default timeout."""
        client = self._make_mock_client()
        self._configure_exec_command(client, exit_code=0)

        executor = SSHExecutor(client)
        executor.run(["long-running-task"], timeout=None)

        client.exec_command.assert_called_once_with("long-running-task", timeout=None)

    def test_run_with_check_raises_on_failure(self):
        client = self._make_mock_client()
        self._configure_exec_command(client, stderr_data="nope", exit_code=1)

        executor = SSHExecutor(client)
        with pytest.raises(CommandError):
            executor.run(["false"], check=True)

    def test_run_nonzero_without_check(self):
        client = self._make_mock_client()
        self._configure_exec_command(client, exit_code=1)

        executor = SSHExecutor(client)
        result = executor.run(["false"])
        assert not result.ok

    def test_close_closes_client(self):
        client = self._make_mock_client()
        executor = SSHExecutor(client)
        executor.close()
        client.close.assert_called_once()

    def test_run_with_input_writes_to_stdin(self):
        client = self._make_mock_client()
        mock_stdin, mock_stdout, mock_stderr = self._configure_exec_command(client, exit_code=0)

        executor = SSHExecutor(client)
        result = executor.run(["tee", "/tmp/test"], input="file content\n")

        assert result.ok
        mock_stdin.write.assert_called_once_with("file content\n")
        mock_stdin.channel.shutdown_write.assert_called_once()

    def test_run_without_input_does_not_write_stdin(self):
        client = self._make_mock_client()
        mock_stdin, _, _ = self._configure_exec_command(client, exit_code=0)

        executor = SSHExecutor(client)
        executor.run(["echo", "hi"])

        mock_stdin.write.assert_not_called()

    def test_rejects_non_ssh_client(self):
        try:
            import paramiko  # noqa: F401
        except ImportError:
            pytest.skip("paramiko not installed")
        with pytest.raises(TypeError, match="SSHClient"):
            SSHExecutor("not a client")


class TestSSHExecutorStream:
    """Tests for SSHExecutor.run_stream()."""

    def _make_mock_client(self):
        """Create a mock paramiko SSHClient."""
        try:
            import paramiko
        except ImportError:
            pytest.skip("paramiko not installed")
        return MagicMock(spec=paramiko.SSHClient)

    def _make_stream_channel(self, stdout_chunks, stderr_chunks=None, exit_code=0):
        """Create a mock channel that yields data chunks via recv()."""
        channel = MagicMock()

        stdout_iter = iter(stdout_chunks or [])
        stderr_iter = iter(stderr_chunks or [])

        call_count = {"n": 0, "stdout_done": False, "stderr_done": False}

        def _recv_ready():
            return not call_count["stdout_done"]

        def _recv(size):
            try:
                data = next(stdout_iter)
                call_count["n"] += 1
                return data
            except StopIteration:
                call_count["stdout_done"] = True
                return b""

        def _recv_stderr_ready():
            return not call_count["stderr_done"]

        def _recv_stderr(size):
            try:
                data = next(stderr_iter)
                call_count["n"] += 1
                return data
            except StopIteration:
                call_count["stderr_done"] = True
                return b""

        def _exit_status_ready():
            return call_count["stdout_done"] and call_count["stderr_done"]

        channel.recv_ready = _recv_ready
        channel.recv = _recv
        channel.recv_stderr_ready = _recv_stderr_ready
        channel.recv_stderr = _recv_stderr
        channel.exit_status_ready = _exit_status_ready
        channel.recv_exit_status.return_value = exit_code

        return channel

    def _attach_channel_to_client(self, client, channel):
        """Wire the channel into the client's transport.open_session()."""
        transport = MagicMock()
        transport.open_session.return_value = channel
        client.get_transport.return_value = transport
        return transport

    def test_returns_exit_code(self):
        client = self._make_mock_client()
        channel = self._make_stream_channel([], exit_code=42)
        self._attach_channel_to_client(client, channel)

        executor = SSHExecutor(client)
        with patch("ots_shared.ssh.executor.select") as mock_select:
            mock_select.select.return_value = ([channel], [], [])
            rc = executor.run_stream(["echo", "hi"])

        assert rc == 42

    def test_writes_stdout_to_buffer(self):
        client = self._make_mock_client()
        channel = self._make_stream_channel([b"hello ", b"world\n"])
        self._attach_channel_to_client(client, channel)

        executor = SSHExecutor(client)
        buf = BytesIO()
        with (
            patch("ots_shared.ssh.executor.select") as mock_select,
            patch("ots_shared.ssh.executor.sys") as mock_sys,
        ):
            mock_select.select.return_value = ([channel], [], [])
            mock_sys.stdout.buffer = buf
            mock_sys.stderr.buffer = BytesIO()
            rc = executor.run_stream(["echo", "hello world"])

        assert buf.getvalue() == b"hello world\n"
        assert rc == 0

    def test_sudo_prefixes_command(self):
        client = self._make_mock_client()
        channel = self._make_stream_channel([], exit_code=0)
        self._attach_channel_to_client(client, channel)

        executor = SSHExecutor(client)
        with patch("ots_shared.ssh.executor.select") as mock_select:
            mock_select.select.return_value = ([channel], [], [])
            executor.run_stream(["journalctl", "-f"], sudo=True)

        channel.exec_command.assert_called_once_with("sudo -- journalctl -f")

    def test_timeout_sets_channel_timeout(self):
        client = self._make_mock_client()
        channel = self._make_stream_channel([], exit_code=0)
        self._attach_channel_to_client(client, channel)

        executor = SSHExecutor(client)
        with patch("ots_shared.ssh.executor.select") as mock_select:
            mock_select.select.return_value = ([channel], [], [])
            executor.run_stream(["sleep", "10"], timeout=300)

        channel.settimeout.assert_called_once_with(300.0)

    def test_stderr_written_to_stderr_buffer(self):
        client = self._make_mock_client()
        channel = self._make_stream_channel([], stderr_chunks=[b"error msg\n"], exit_code=1)
        self._attach_channel_to_client(client, channel)

        executor = SSHExecutor(client)
        stderr_buf = BytesIO()
        with (
            patch("ots_shared.ssh.executor.select") as mock_select,
            patch("ots_shared.ssh.executor.sys") as mock_sys,
        ):
            mock_select.select.return_value = ([channel], [], [])
            mock_sys.stdout.buffer = BytesIO()
            mock_sys.stderr.buffer = stderr_buf
            rc = executor.run_stream(["bad_cmd"])

        assert stderr_buf.getvalue() == b"error msg\n"
        assert rc == 1

    def test_deadline_exceeded_returns_124_and_closes_channel(self):
        client = self._make_mock_client()
        channel = MagicMock()
        channel.exit_status_ready.return_value = False
        channel.recv_ready.return_value = False
        channel.recv_stderr_ready.return_value = False
        self._attach_channel_to_client(client, channel)

        executor = SSHExecutor(client)
        with (
            patch("time.monotonic", side_effect=[1000.0, 1031.0]),
            patch("ots_shared.ssh.executor.select") as mock_select,
        ):
            mock_select.select.return_value = ([], [], [])
            rc = executor.run_stream(["long-running-task"], timeout=30)

        assert rc == 124
        channel.close.assert_called_once()


class TestSSHExecutorInteractive:
    """Tests for SSHExecutor.run_interactive()."""

    def _make_mock_client(self):
        """Create a mock paramiko SSHClient."""
        try:
            import paramiko
        except ImportError:
            pytest.skip("paramiko not installed")
        return MagicMock(spec=paramiko.SSHClient)

    def _setup_interactive(self, client, exit_code=0):
        """Wire a mock channel into the client for run_interactive."""
        channel = MagicMock()
        channel.recv_exit_status.return_value = exit_code
        transport = MagicMock()
        transport.open_session.return_value = channel
        client.get_transport.return_value = transport
        return transport, channel

    def test_opens_pty_session(self):
        """Should call transport.open_session(), get_pty(), and exec_command()."""
        client = self._make_mock_client()
        transport, channel = self._setup_interactive(client, exit_code=0)

        executor = SSHExecutor(client)
        with (
            patch("ots_shared.ssh._pty.get_terminal_size", return_value=(120, 40)),
            patch("ots_shared.ssh._pty.run_pty_session", return_value=0),
            patch.dict(os.environ, {"TERM": "xterm-256color"}),
        ):
            executor.run_interactive(["bash"])

        transport.open_session.assert_called_once()
        channel.get_pty.assert_called_once_with(term="xterm-256color", width=120, height=40)
        channel.exec_command.assert_called_once_with("bash")
        channel.setblocking.assert_called_once_with(0)
        channel.close.assert_called_once()

    def test_returns_exit_code(self):
        """Should return the exit code from _pty.run_pty_session."""
        client = self._make_mock_client()
        _transport, _channel = self._setup_interactive(client, exit_code=42)

        executor = SSHExecutor(client)
        with (
            patch("ots_shared.ssh._pty.get_terminal_size", return_value=(80, 24)),
            patch("ots_shared.ssh._pty.run_pty_session", return_value=42),
        ):
            rc = executor.run_interactive(["bash"])

        assert rc == 42

    def test_sudo_prefixes_command(self):
        """sudo=True should prepend 'sudo --' to the command."""
        client = self._make_mock_client()
        _transport, channel = self._setup_interactive(client, exit_code=0)

        executor = SSHExecutor(client)
        with (
            patch("ots_shared.ssh._pty.get_terminal_size", return_value=(80, 24)),
            patch("ots_shared.ssh._pty.run_pty_session", return_value=0),
        ):
            executor.run_interactive(["podman", "exec", "-it", "ctr", "/bin/sh"], sudo=True)

        channel.exec_command.assert_called_once_with("sudo -- podman exec -it ctr /bin/sh")

    def test_delegates_to_pty_module(self):
        """Should delegate the PTY session to _pty.run_pty_session."""
        client = self._make_mock_client()
        _transport, channel = self._setup_interactive(client, exit_code=0)

        executor = SSHExecutor(client)
        with (
            patch("ots_shared.ssh._pty.get_terminal_size", return_value=(80, 24)),
            patch("ots_shared.ssh._pty.run_pty_session", return_value=0) as mock_pty_session,
        ):
            executor.run_interactive(["bash"])

        mock_pty_session.assert_called_once_with(channel)

    def test_uses_term_env_var(self):
        """Should use $TERM from environment for the PTY request."""
        client = self._make_mock_client()
        _transport, channel = self._setup_interactive(client, exit_code=0)

        executor = SSHExecutor(client)
        with (
            patch("ots_shared.ssh._pty.get_terminal_size", return_value=(80, 24)),
            patch("ots_shared.ssh._pty.run_pty_session", return_value=0),
            patch.dict(os.environ, {"TERM": "screen-256color"}),
        ):
            executor.run_interactive(["bash"])

        channel.get_pty.assert_called_once_with(term="screen-256color", width=80, height=24)

    def test_defaults_term_to_xterm_256color(self):
        """Should default to xterm-256color when $TERM is not set."""
        client = self._make_mock_client()
        _transport, channel = self._setup_interactive(client, exit_code=0)

        executor = SSHExecutor(client)
        with (
            patch("ots_shared.ssh._pty.get_terminal_size", return_value=(80, 24)),
            patch("ots_shared.ssh._pty.run_pty_session", return_value=0),
            patch.dict(os.environ, {}, clear=True),
        ):
            executor.run_interactive(["bash"])

        # When TERM is not in env, should fall back to xterm-256color
        call_kwargs = channel.get_pty.call_args.kwargs
        assert call_kwargs["term"] == "xterm-256color"

    def test_channel_closed_even_on_pty_error(self):
        """Channel should be closed even when run_pty_session raises."""
        client = self._make_mock_client()
        _transport, channel = self._setup_interactive(client, exit_code=0)

        executor = SSHExecutor(client)
        with (
            patch("ots_shared.ssh._pty.get_terminal_size", return_value=(80, 24)),
            patch("ots_shared.ssh._pty.run_pty_session", side_effect=RuntimeError("PTY error")),
        ):
            with pytest.raises(RuntimeError, match="PTY error"):
                executor.run_interactive(["bash"])

        channel.close.assert_called_once()


class TestLocalExecutorFileTransfer:
    """Tests for LocalExecutor.put_file() and get_file()."""

    def test_put_file_copies_content(self, tmp_path):
        src = tmp_path / "src.txt"
        dst = tmp_path / "subdir" / "dst.txt"
        src.write_text("hello world")

        executor = LocalExecutor()
        executor.put_file(src, dst)

        assert dst.read_text() == "hello world"

    def test_put_file_sets_permissions(self, tmp_path):
        src = tmp_path / "src.txt"
        dst = tmp_path / "dst.txt"
        src.write_text("secret")

        executor = LocalExecutor()
        executor.put_file(src, dst, permissions=0o600)

        assert dst.read_text() == "secret"
        assert oct(dst.stat().st_mode & 0o777) == oct(0o600)

    def test_put_file_creates_parent_dirs(self, tmp_path):
        src = tmp_path / "src.txt"
        dst = tmp_path / "a" / "b" / "c" / "dst.txt"
        src.write_bytes(b"\x00\x01\x02")

        executor = LocalExecutor()
        executor.put_file(src, dst)

        assert dst.read_bytes() == b"\x00\x01\x02"

    def test_get_file_copies_content(self, tmp_path):
        src = tmp_path / "remote.txt"
        dst = tmp_path / "local.txt"
        src.write_text("data from remote")

        executor = LocalExecutor()
        executor.get_file(src, dst)

        assert dst.read_text() == "data from remote"

    def test_get_file_creates_parent_dirs(self, tmp_path):
        src = tmp_path / "remote.txt"
        dst = tmp_path / "deep" / "path" / "local.txt"
        src.write_bytes(b"binary data")

        executor = LocalExecutor()
        executor.get_file(src, dst)

        assert dst.read_bytes() == b"binary data"


class TestSSHExecutorFileTransfer:
    """Tests for SSHExecutor.put_file() and get_file()."""

    def _make_mock_client(self):
        try:
            import paramiko
        except ImportError:
            pytest.skip("paramiko not installed")
        return MagicMock(spec=paramiko.SSHClient)

    def test_put_file_uses_sftp(self, tmp_path):
        client = self._make_mock_client()
        mock_sftp = MagicMock()
        client.open_sftp.return_value = mock_sftp

        src = tmp_path / "local.txt"
        src.write_text("content")

        executor = SSHExecutor(client)
        executor.put_file(src, "/remote/path/file.txt")

        client.open_sftp.assert_called_once()
        mock_sftp.put.assert_called_once_with(str(src), "/remote/path/file.txt")
        mock_sftp.close.assert_called_once()

    def test_put_file_sets_permissions(self, tmp_path):
        client = self._make_mock_client()
        mock_sftp = MagicMock()
        client.open_sftp.return_value = mock_sftp

        src = tmp_path / "local.txt"
        src.write_text("secret")

        executor = SSHExecutor(client)
        executor.put_file(src, "/remote/secret.txt", permissions=0o600)

        mock_sftp.chmod.assert_called_once_with("/remote/secret.txt", 0o600)

    def test_put_file_no_permissions_skips_chmod(self, tmp_path):
        client = self._make_mock_client()
        mock_sftp = MagicMock()
        client.open_sftp.return_value = mock_sftp

        src = tmp_path / "local.txt"
        src.write_text("data")

        executor = SSHExecutor(client)
        executor.put_file(src, "/remote/file.txt")

        mock_sftp.chmod.assert_not_called()

    def test_put_file_closes_sftp_on_error(self, tmp_path):
        client = self._make_mock_client()
        mock_sftp = MagicMock()
        mock_sftp.put.side_effect = OSError("transfer failed")
        client.open_sftp.return_value = mock_sftp

        src = tmp_path / "local.txt"
        src.write_text("data")

        executor = SSHExecutor(client)
        with pytest.raises(IOError):
            executor.put_file(src, "/remote/file.txt")

        mock_sftp.close.assert_called_once()

    def test_get_file_uses_sftp(self, tmp_path):
        client = self._make_mock_client()
        mock_sftp = MagicMock()
        client.open_sftp.return_value = mock_sftp

        dst = tmp_path / "local.txt"

        executor = SSHExecutor(client)
        executor.get_file("/remote/file.txt", dst)

        client.open_sftp.assert_called_once()
        mock_sftp.get.assert_called_once_with("/remote/file.txt", str(dst))
        mock_sftp.close.assert_called_once()

    def test_get_file_closes_sftp_on_error(self, tmp_path):
        client = self._make_mock_client()
        mock_sftp = MagicMock()
        mock_sftp.get.side_effect = OSError("file not found")
        client.open_sftp.return_value = mock_sftp

        dst = tmp_path / "local.txt"

        executor = SSHExecutor(client)
        with pytest.raises(IOError):
            executor.get_file("/remote/missing.txt", dst)

        mock_sftp.close.assert_called_once()


class TestLocalExecutorStringGuard:
    """LocalExecutor rejects raw strings to prevent shlex.quote char-iteration."""

    def test_run_rejects_string(self):
        executor = LocalExecutor()
        with pytest.raises(TypeError, match="list\\[str\\]"):
            executor.run("echo hi")  # type: ignore[arg-type]

    def test_run_stream_rejects_string(self):
        executor = LocalExecutor()
        with pytest.raises(TypeError, match="list\\[str\\]"):
            executor.run_stream("echo hi")  # type: ignore[arg-type]

    def test_run_interactive_rejects_string(self):
        executor = LocalExecutor()
        with pytest.raises(TypeError, match="list\\[str\\]"):
            executor.run_interactive("echo hi")  # type: ignore[arg-type]


class TestSSHExecutorStringGuard:
    """SSHExecutor rejects raw strings to prevent shlex.quote char-iteration."""

    def _make_mock_client(self):
        try:
            import paramiko
        except ImportError:
            pytest.skip("paramiko not installed")
        return MagicMock(spec=paramiko.SSHClient)

    def test_run_rejects_string(self):
        client = self._make_mock_client()
        executor = SSHExecutor(client)
        with pytest.raises(TypeError, match="list\\[str\\]"):
            executor.run("echo hi")  # type: ignore[arg-type]

    def test_run_stream_rejects_string(self):
        client = self._make_mock_client()
        executor = SSHExecutor(client)
        with pytest.raises(TypeError, match="list\\[str\\]"):
            executor.run_stream("echo hi")  # type: ignore[arg-type]

    def test_run_interactive_rejects_string(self):
        client = self._make_mock_client()
        executor = SSHExecutor(client)
        with pytest.raises(TypeError, match="list\\[str\\]"):
            executor.run_interactive("echo hi")  # type: ignore[arg-type]


class TestIsRemote:
    """Tests for the is_remote() helper function."""

    def test_none_returns_false(self):
        """is_remote(None) should return False (no executor means local)."""
        assert is_remote(None) is False

    def test_local_executor_returns_false(self):
        """is_remote(LocalExecutor()) should return False."""
        assert is_remote(LocalExecutor()) is False

    def test_ssh_executor_returns_true(self):
        """is_remote(SSHExecutor(...)) should return True."""
        try:
            import paramiko
        except ImportError:
            pytest.skip("paramiko not installed")

        client = MagicMock(spec=paramiko.SSHClient)
        assert is_remote(SSHExecutor(client)) is True

    def test_also_exported_from_package_init(self):
        """is_remote should be importable from ots_shared.ssh."""
        from ots_shared.ssh import is_remote as pkg_is_remote

        assert pkg_is_remote is is_remote
