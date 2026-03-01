"""Command execution abstraction for local and remote (SSH) targets.

Provides a Protocol-based executor pattern so callers can run shell commands
without knowing whether they execute locally or over SSH.  Also supports
individual file transfers via ``put_file`` / ``get_file`` (SFTP for SSH,
local filesystem for local).  For bulk file operations, use rsync
(see ``ots_containers.commands.host._rsync``).
"""

from __future__ import annotations

import logging
import os
import select
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

# Default timeout (in seconds) for SSH command execution.
# Prevents hung remote processes from running indefinitely.
# Individual call sites can override with an explicit timeout= kwarg.
# Use None for truly open-ended operations (run_interactive handles this).
SSH_DEFAULT_TIMEOUT: int = 120

logger = logging.getLogger(__name__)

REDACTED = "***"


def _redact_cmd(cmd: list[str], sensitive: set[str] | None) -> list[str]:
    """Return a copy of *cmd* with sensitive argument values replaced."""
    if not sensitive:
        return cmd
    return [REDACTED if c in sensitive else c for c in cmd]


@dataclass(frozen=True)
class Result:
    """Outcome of a command execution."""

    command: str
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def check(self) -> None:
        """Raise CommandError if the command failed."""
        if not self.ok:
            raise CommandError(self)


class CommandError(Exception):
    """A command returned a non-zero exit code."""

    def __init__(self, result: Result) -> None:
        self.result = result
        super().__init__(f"Command failed (exit {result.returncode}): {result.command}")


@runtime_checkable
class Executor(Protocol):
    """Interface for running shell commands on a target host."""

    def run(
        self,
        cmd: list[str],
        *,
        sudo: bool = False,
        timeout: int | None = None,
        check: bool = False,
        input: str | None = None,
        sensitive_args: set[str] | None = None,
    ) -> Result: ...

    def run_stream(
        self,
        cmd: list[str],
        *,
        sudo: bool = False,
        timeout: int | None = None,
        sensitive_args: set[str] | None = None,
    ) -> int:
        """Stream stdout/stderr to the caller's terminal in real time.

        Returns the process exit code. Does not capture output — it flows
        directly to the current terminal.
        """
        ...

    def run_interactive(
        self,
        cmd: list[str],
        *,
        sudo: bool = False,
        sensitive_args: set[str] | None = None,
    ) -> int:
        """Run with full PTY: bidirectional stdin/stdout, SIGWINCH propagation.

        For interactive sessions (shells, exec -it) — no timeout since the
        session is open-ended. Returns the process exit code.
        """
        ...

    def put_file(
        self,
        local_path: str | Path,
        remote_path: str | Path,
        *,
        permissions: int | None = None,
    ) -> None:
        """Transfer a file from the local machine to the target host.

        Args:
            local_path: Path to the file on the local machine.
            remote_path: Destination path on the target host.
            permissions: Optional octal permissions (e.g. 0o644) to set
                on the destination file.
        """
        ...

    def get_file(
        self,
        remote_path: str | Path,
        local_path: str | Path,
    ) -> None:
        """Transfer a file from the target host to the local machine.

        Args:
            remote_path: Path to the file on the target host.
            local_path: Destination path on the local machine.
        """
        ...

    def close(self) -> None: ...


def _require_list(cmd: object, method: str) -> None:
    """Raise TypeError if *cmd* is a str instead of list[str].

    shlex.quote iterates a str character-by-character, producing a broken
    (though not injectable) command.  Catching this early prevents subtle bugs.
    """
    if isinstance(cmd, str):
        raise TypeError(
            f"{method} requires cmd as list[str], got str. "
            f"Use shlex.split() or pass a list: {cmd!r}"
        )


def is_remote(executor: Executor | None) -> bool:
    """Return True if *executor* dispatches commands to a remote host.

    Returns False for ``None`` (no executor) or a :class:`LocalExecutor`.
    """
    if executor is None:
        return False
    return not isinstance(executor, LocalExecutor)


class LocalExecutor:
    """Execute commands on the local machine via subprocess.

    File transfers operate directly on the local filesystem.
    """

    def run(
        self,
        cmd: list[str],
        *,
        sudo: bool = False,
        timeout: int | None = None,
        check: bool = False,
        input: str | None = None,
        sensitive_args: set[str] | None = None,
    ) -> Result:
        _require_list(cmd, "LocalExecutor.run")
        full_cmd = ["sudo", "--"] + cmd if sudo else cmd
        safe_cmd = _redact_cmd(full_cmd, sensitive_args)
        logger.debug("local: %s", " ".join(shlex.quote(c) for c in safe_cmd))
        try:
            kwargs: dict = dict(
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            if input is not None:
                kwargs["input"] = input
            proc = subprocess.run(full_cmd, **kwargs)
        except subprocess.TimeoutExpired as exc:
            return Result(
                command=" ".join(shlex.quote(c) for c in safe_cmd),
                returncode=124,
                stdout=exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or ""),
                stderr=exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or ""),
            )
        result = Result(
            command=" ".join(shlex.quote(c) for c in safe_cmd),
            returncode=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
        )
        if check:
            result.check()
        return result

    def run_stream(
        self,
        cmd: list[str],
        *,
        sudo: bool = False,
        timeout: int | None = None,
        sensitive_args: set[str] | None = None,
    ) -> int:
        _require_list(cmd, "LocalExecutor.run_stream")
        full_cmd = ["sudo", "--"] + cmd if sudo else cmd
        safe_cmd = _redact_cmd(full_cmd, sensitive_args)
        logger.debug("local stream: %s", " ".join(shlex.quote(c) for c in safe_cmd))
        try:
            proc = subprocess.run(full_cmd, timeout=timeout)
        except subprocess.TimeoutExpired:
            return 124
        return proc.returncode

    def run_interactive(
        self,
        cmd: list[str],
        *,
        sudo: bool = False,
        sensitive_args: set[str] | None = None,
    ) -> int:
        _require_list(cmd, "LocalExecutor.run_interactive")
        full_cmd = ["sudo", "--"] + cmd if sudo else cmd
        safe_cmd = _redact_cmd(full_cmd, sensitive_args)
        logger.debug("local interactive: %s", " ".join(shlex.quote(c) for c in safe_cmd))
        try:
            proc = subprocess.run(full_cmd)
            return proc.returncode
        except KeyboardInterrupt:
            return 130

    def put_file(
        self,
        local_path: str | Path,
        remote_path: str | Path,
        *,
        permissions: int | None = None,
    ) -> None:
        src = Path(local_path)
        dst = Path(remote_path)
        logger.debug("local put_file: %s -> %s", src, dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())
        if permissions is not None:
            dst.chmod(permissions)

    def get_file(
        self,
        remote_path: str | Path,
        local_path: str | Path,
    ) -> None:
        src = Path(remote_path)
        dst = Path(local_path)
        logger.debug("local get_file: %s -> %s", src, dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())

    def close(self) -> None:
        pass


class SSHExecutor:
    """Execute commands on a remote host over an SSH connection.

    Requires paramiko. Import is deferred so the module can be loaded
    without paramiko installed — only construction raises ImportError.
    """

    def __init__(self, client: object) -> None:
        try:
            import paramiko
        except ImportError:
            raise ImportError(
                "paramiko is required for SSH execution. "
                "Install it with: pip install ots-shared[ssh]"
            ) from None
        if not isinstance(client, paramiko.SSHClient):
            raise TypeError(f"Expected paramiko.SSHClient, got {type(client).__name__}")
        self._client = client

    # Sentinel to distinguish "caller did not pass timeout" from "caller passed None".
    _UNSET = object()

    def run(
        self,
        cmd: list[str],
        *,
        sudo: bool = False,
        timeout: int | None | object = _UNSET,
        check: bool = False,
        input: str | None = None,
        sensitive_args: set[str] | None = None,
    ) -> Result:
        _require_list(cmd, "SSHExecutor.run")

        # Apply default timeout when caller does not specify one.
        # Pass timeout=None explicitly to disable the timeout.
        effective_timeout: int | None
        if timeout is self._UNSET:
            effective_timeout = SSH_DEFAULT_TIMEOUT
        else:
            effective_timeout = timeout  # type: ignore[assignment]

        # Security: shlex.quote every argument before joining
        shell_cmd = " ".join(shlex.quote(c) for c in cmd)
        safe_cmd = " ".join(shlex.quote(c) for c in _redact_cmd(cmd, sensitive_args))
        if sudo:
            shell_cmd = f"sudo -- {shell_cmd}"
            safe_cmd = f"sudo -- {safe_cmd}"
        logger.debug("ssh: %s", safe_cmd)
        stdin_ch, stdout, stderr = self._client.exec_command(shell_cmd, timeout=effective_timeout)
        if input is not None:
            stdin_ch.write(input)
            stdin_ch.channel.shutdown_write()
        exit_code = stdout.channel.recv_exit_status()
        result = Result(
            command=safe_cmd,
            returncode=exit_code,
            stdout=stdout.read().decode("utf-8", errors="replace"),
            stderr=stderr.read().decode("utf-8", errors="replace"),
        )
        if check:
            result.check()
        return result

    def run_stream(
        self,
        cmd: list[str],
        *,
        sudo: bool = False,
        timeout: int | None = None,
        sensitive_args: set[str] | None = None,
    ) -> int:
        """Stream remote command output to local terminal via select loop.

        When *timeout* is provided, enforces an overall deadline on the
        streaming session. If the deadline is exceeded, the channel is
        closed and exit code 124 (matching GNU timeout convention) is returned.
        """
        import time

        _require_list(cmd, "SSHExecutor.run_stream")
        shell_cmd = " ".join(shlex.quote(c) for c in cmd)
        safe_cmd = " ".join(shlex.quote(c) for c in _redact_cmd(cmd, sensitive_args))
        if sudo:
            shell_cmd = f"sudo -- {shell_cmd}"
            safe_cmd = f"sudo -- {safe_cmd}"
        logger.debug("ssh stream: %s", safe_cmd)
        transport = self._client.get_transport()
        channel = transport.open_session()
        channel.setblocking(0)
        if timeout is not None:
            channel.settimeout(float(timeout))
        channel.exec_command(shell_cmd)

        # Overall deadline for the select loop
        deadline = time.monotonic() + timeout if timeout is not None else None

        # Stream output until channel is closed
        while (
            not channel.exit_status_ready() or channel.recv_ready() or channel.recv_stderr_ready()
        ):
            # Check deadline
            if deadline is not None and time.monotonic() > deadline:
                logger.warning("ssh stream timeout after %ds: %s", timeout, safe_cmd)
                channel.close()
                return 124

            readable, _, _ = select.select([channel], [], [], 0.1)
            if readable:
                if channel.recv_ready():
                    data = channel.recv(4096)
                    if data:
                        sys.stdout.buffer.write(data)
                        sys.stdout.buffer.flush()
                if channel.recv_stderr_ready():
                    data = channel.recv_stderr(4096)
                    if data:
                        sys.stderr.buffer.write(data)
                        sys.stderr.buffer.flush()
        # Drain any remaining data after exit
        while channel.recv_ready():
            data = channel.recv(4096)
            if data:
                sys.stdout.buffer.write(data)
                sys.stdout.buffer.flush()
        while channel.recv_stderr_ready():
            data = channel.recv_stderr(4096)
            if data:
                sys.stderr.buffer.write(data)
                sys.stderr.buffer.flush()
        exit_code = channel.recv_exit_status()
        channel.close()
        return exit_code

    def run_interactive(
        self,
        cmd: list[str],
        *,
        sudo: bool = False,
        sensitive_args: set[str] | None = None,
    ) -> int:
        """Run with full PTY over SSH: bidirectional stdin/stdout, SIGWINCH.

        Delegates terminal handling to :mod:`ots_shared.ssh._pty`.
        """
        _require_list(cmd, "SSHExecutor.run_interactive")
        from ots_shared.ssh import _pty

        shell_cmd = " ".join(shlex.quote(c) for c in cmd)
        safe_cmd = " ".join(shlex.quote(c) for c in _redact_cmd(cmd, sensitive_args))
        if sudo:
            shell_cmd = f"sudo -- {shell_cmd}"
            safe_cmd = f"sudo -- {safe_cmd}"
        logger.debug("ssh interactive: %s", safe_cmd)

        transport = self._client.get_transport()
        channel = transport.open_session()

        # Request PTY with current terminal dimensions
        cols, rows = _pty.get_terminal_size()
        term = os.environ.get("TERM", "xterm-256color")
        channel.get_pty(term=term, width=cols, height=rows)
        channel.exec_command(shell_cmd)
        channel.setblocking(0)

        try:
            exit_code = _pty.run_pty_session(channel)
        finally:
            channel.close()
        return exit_code

    def put_file(
        self,
        local_path: str | Path,
        remote_path: str | Path,
        *,
        permissions: int | None = None,
    ) -> None:
        src = Path(local_path)
        dst_str = str(remote_path)
        logger.debug("ssh put_file: %s -> %s", src, dst_str)
        sftp = self._client.open_sftp()
        try:
            sftp.put(str(src), dst_str)
            if permissions is not None:
                sftp.chmod(dst_str, permissions)
        finally:
            sftp.close()

    def get_file(
        self,
        remote_path: str | Path,
        local_path: str | Path,
    ) -> None:
        src_str = str(remote_path)
        dst = Path(local_path)
        logger.debug("ssh get_file: %s -> %s", src_str, dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        sftp = self._client.open_sftp()
        try:
            sftp.get(src_str, str(dst))
        finally:
            sftp.close()

    def close(self) -> None:
        self._client.close()
