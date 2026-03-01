"""PTY helper for interactive SSH sessions.

Internal module — not exported from ots_shared.ssh.__init__.
Provides terminal save/restore, raw mode, SIGWINCH propagation,
and a bidirectional select loop for full-PTY SSH channels.

All termios/tty/signal usage is guarded for portability (Windows
lacks these modules). Functions raise RuntimeError if called on
a platform without PTY support.
"""

from __future__ import annotations

import logging
import os
import select
import shutil
import sys

logger = logging.getLogger(__name__)

# Guarded imports — these are Unix-only.
try:
    import signal
    import termios
    import tty

    _HAS_PTY_SUPPORT = True
except ImportError:
    _HAS_PTY_SUPPORT = False


def _require_pty_support() -> None:
    """Raise RuntimeError if termios/tty are unavailable."""
    if not _HAS_PTY_SUPPORT:
        raise RuntimeError(
            "PTY support requires termios/tty (Unix-only). "
            "Interactive SSH sessions are not available on this platform."
        )


def get_terminal_size() -> tuple[int, int]:
    """Return (columns, rows) of the current terminal.

    Falls back to (80, 24) if detection fails.
    """
    size = shutil.get_terminal_size(fallback=(80, 24))
    return (size.columns, size.lines)


def set_raw(fd: int) -> list:
    """Save terminal attributes and switch *fd* to raw mode.

    Returns the saved attributes (pass to :func:`restore`).
    Raises RuntimeError on platforms without termios.
    """
    _require_pty_support()
    old_attrs = termios.tcgetattr(fd)
    tty.setraw(fd)
    tty.setcbreak(fd)
    return old_attrs


def restore(fd: int, attrs: list) -> None:
    """Restore terminal attributes previously saved by :func:`set_raw`.

    Uses TCSADRAIN so queued output finishes before the change.
    Raises RuntimeError on platforms without termios.
    """
    _require_pty_support()
    termios.tcsetattr(fd, termios.TCSADRAIN, attrs)


def install_sigwinch_handler(channel: object) -> object | None:
    """Install a SIGWINCH handler that resizes *channel*'s PTY.

    Returns the previous signal handler so callers can restore it.
    On platforms without SIGWINCH this is a no-op and returns None.
    """
    if not _HAS_PTY_SUPPORT:
        return None

    if not hasattr(signal, "SIGWINCH"):
        return None

    def _handler(signum: int, frame: object) -> None:
        try:
            cols, rows = get_terminal_size()
            channel.resize_pty(width=cols, height=rows)  # type: ignore[union-attr]
        except OSError:
            pass

    old_handler = signal.signal(signal.SIGWINCH, _handler)
    return old_handler


def restore_sigwinch_handler(old_handler: object | None) -> None:
    """Restore the SIGWINCH handler saved by :func:`install_sigwinch_handler`."""
    if old_handler is None:
        return
    if not _HAS_PTY_SUPPORT:
        return
    if not hasattr(signal, "SIGWINCH"):
        return
    signal.signal(signal.SIGWINCH, old_handler)  # type: ignore[arg-type]


def interactive_loop(
    channel: object,
    stdin_fd: int,
    stdout_buffer: object | None = None,
) -> int:
    """Bidirectional select loop between *channel* and *stdin_fd*.

    Reads from the Paramiko channel and writes to *stdout_buffer*
    (defaults to ``sys.stdout.buffer``). Reads from *stdin_fd* and
    sends to the channel. Runs until the channel exits.

    Returns the remote process exit code.
    """
    if stdout_buffer is None:
        stdout_buffer = sys.stdout.buffer

    while True:
        readable, _, _ = select.select([channel, stdin_fd], [], [], 0.1)

        if channel in readable:
            if channel.recv_ready():  # type: ignore[union-attr]
                data = channel.recv(4096)  # type: ignore[union-attr]
                if data:
                    stdout_buffer.write(data)  # type: ignore[union-attr]
                    stdout_buffer.flush()  # type: ignore[union-attr]
                elif channel.exit_status_ready():  # type: ignore[union-attr]
                    break
            elif channel.exit_status_ready():  # type: ignore[union-attr]
                break

        if stdin_fd in readable:
            user_input = os.read(stdin_fd, 1024)
            if user_input:
                channel.sendall(user_input)  # type: ignore[union-attr]

        if channel.exit_status_ready() and not channel.recv_ready():  # type: ignore[union-attr]
            break

    return channel.recv_exit_status()  # type: ignore[union-attr]


def run_pty_session(channel: object) -> int:
    """High-level PTY session driver for SSHExecutor.run_interactive().

    Saves terminal state, sets raw mode, installs SIGWINCH handler,
    runs the bidirectional loop, and restores everything in ``finally``.

    Returns the remote process exit code.
    """
    _require_pty_support()

    stdin_fd = sys.stdin.fileno()
    old_attrs = termios.tcgetattr(stdin_fd)
    old_sigwinch = None

    try:
        old_sigwinch = install_sigwinch_handler(channel)
        tty.setraw(stdin_fd)
        tty.setcbreak(stdin_fd)
        exit_code = interactive_loop(channel, stdin_fd)
    finally:
        termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old_attrs)
        restore_sigwinch_handler(old_sigwinch)

    return exit_code
