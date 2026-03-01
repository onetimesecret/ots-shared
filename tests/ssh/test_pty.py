"""Tests for ots_shared.ssh._pty module.

All termios/tty/signal interactions are mocked — these tests run on any
platform without an actual terminal.
"""

import os
from unittest.mock import MagicMock, patch


class TestGetTerminalSize:
    """Tests for get_terminal_size()."""

    def test_returns_int_tuple(self):
        from ots_shared.ssh._pty import get_terminal_size

        cols, rows = get_terminal_size()
        assert isinstance(cols, int)
        assert isinstance(rows, int)
        assert cols > 0
        assert rows > 0

    def test_fallback_to_80x24(self):
        """When terminal detection fails, should return (80, 24)."""
        from ots_shared.ssh._pty import get_terminal_size

        with patch("ots_shared.ssh._pty.shutil.get_terminal_size") as mock_size:
            # shutil.get_terminal_size uses the fallback when detection fails
            mock_size.return_value = os.terminal_size((80, 24))
            cols, rows = get_terminal_size()
            assert (cols, rows) == (80, 24)


class TestSetRaw:
    """Tests for set_raw()."""

    def test_saves_and_sets_attrs(self):
        """set_raw should save attrs via tcgetattr, then setraw + setcbreak."""
        from ots_shared.ssh._pty import set_raw

        saved_attrs = [1, 2, 3, 4, 5, 6, [7]]
        with (
            patch("ots_shared.ssh._pty.termios") as mock_termios,
            patch("ots_shared.ssh._pty.tty") as mock_tty,
        ):
            mock_termios.tcgetattr.return_value = saved_attrs

            result = set_raw(0)

            mock_termios.tcgetattr.assert_called_once_with(0)
            mock_tty.setraw.assert_called_once_with(0)
            mock_tty.setcbreak.assert_called_once_with(0)
            assert result == saved_attrs


class TestRestore:
    """Tests for restore()."""

    def test_calls_tcsetattr_with_saved_attrs(self):
        """restore should call tcsetattr with TCSADRAIN and the saved attrs."""
        from ots_shared.ssh._pty import restore

        saved_attrs = [1, 2, 3, 4, 5, 6, [7]]
        with patch("ots_shared.ssh._pty.termios") as mock_termios:
            restore(0, saved_attrs)

            mock_termios.tcsetattr.assert_called_once_with(0, mock_termios.TCSADRAIN, saved_attrs)


class TestInteractiveLoop:
    """Tests for interactive_loop()."""

    def test_exits_when_channel_closed(self):
        """interactive_loop should exit when channel reports exit ready."""
        from ots_shared.ssh._pty import interactive_loop

        channel = MagicMock()
        # First call to select returns channel readable, but no data and exit ready
        channel.recv_ready.return_value = False
        channel.exit_status_ready.return_value = True
        channel.recv_exit_status.return_value = 0

        stdout_buf = MagicMock()

        with patch("ots_shared.ssh._pty.select.select", return_value=([channel], [], [])):
            rc = interactive_loop(channel, stdin_fd=999, stdout_buffer=stdout_buf)

        assert rc == 0
        channel.recv_exit_status.assert_called_once()

    def test_forwards_channel_data_to_stdout(self):
        """interactive_loop should write channel data to stdout_buffer."""
        from ots_shared.ssh._pty import interactive_loop

        channel = MagicMock()
        call_count = 0

        def recv_ready_side_effect():
            nonlocal call_count
            call_count += 1
            # First call: data available. After that: no data.
            return call_count <= 1

        channel.recv_ready.side_effect = recv_ready_side_effect
        channel.recv.return_value = b"hello\n"

        exit_ready_count = 0

        def exit_status_ready_effect():
            nonlocal exit_ready_count
            exit_ready_count += 1
            return exit_ready_count > 2

        channel.exit_status_ready.side_effect = exit_status_ready_effect
        channel.recv_exit_status.return_value = 0

        stdout_buf = MagicMock()

        with patch("ots_shared.ssh._pty.select.select", return_value=([channel], [], [])):
            rc = interactive_loop(channel, stdin_fd=999, stdout_buffer=stdout_buf)

        assert rc == 0
        stdout_buf.write.assert_called_with(b"hello\n")
        stdout_buf.flush.assert_called()


class TestSigwinchHandler:
    """Tests for SIGWINCH handler installation and restoration."""

    def test_install_sigwinch_handler_calls_resize_pty(self):
        """Installed handler should call channel.resize_pty on SIGWINCH."""
        from ots_shared.ssh._pty import install_sigwinch_handler

        channel = MagicMock()
        with (
            patch("ots_shared.ssh._pty.signal") as mock_signal,
            patch("ots_shared.ssh._pty.get_terminal_size", return_value=(120, 40)),
        ):
            mock_signal.SIGWINCH = 28
            old_handler = MagicMock()
            mock_signal.signal.return_value = old_handler

            result = install_sigwinch_handler(channel)

            # signal.signal was called with SIGWINCH and a handler function
            mock_signal.signal.assert_called_once()
            call_args = mock_signal.signal.call_args
            assert call_args[0][0] == 28  # SIGWINCH

            # Simulate SIGWINCH by calling the handler
            handler_fn = call_args[0][1]
            handler_fn(28, None)

            channel.resize_pty.assert_called_once_with(width=120, height=40)
            assert result is old_handler

    def test_restore_sigwinch_handler(self):
        """restore_sigwinch_handler should restore the previous handler."""
        from ots_shared.ssh._pty import restore_sigwinch_handler

        old_handler = MagicMock()
        with patch("ots_shared.ssh._pty.signal") as mock_signal:
            mock_signal.SIGWINCH = 28
            restore_sigwinch_handler(old_handler)

            mock_signal.signal.assert_called_once_with(28, old_handler)

    def test_restore_sigwinch_handler_noop_for_none(self):
        """restore_sigwinch_handler(None) should be a no-op."""
        from ots_shared.ssh._pty import restore_sigwinch_handler

        # Should not raise
        restore_sigwinch_handler(None)
