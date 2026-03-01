"""Tests for ots_shared.exit_codes module."""

from ots_shared.exit_codes import EXIT_FAILURE, EXIT_PARTIAL, EXIT_PRECOND, EXIT_SUCCESS


class TestExitCodeValues:
    """Verify exit code values match POSIX conventions."""

    def test_success_is_zero(self):
        assert EXIT_SUCCESS == 0

    def test_failure_is_one(self):
        assert EXIT_FAILURE == 1

    def test_partial_is_two(self):
        assert EXIT_PARTIAL == 2

    def test_precond_is_three(self):
        assert EXIT_PRECOND == 3


class TestExitCodeUniqueness:
    """All exit codes must be distinct."""

    def test_all_codes_unique(self):
        codes = [EXIT_SUCCESS, EXIT_FAILURE, EXIT_PARTIAL, EXIT_PRECOND]
        assert len(codes) == len(set(codes))


class TestExitCodeTypes:
    """Exit codes must be integers for use with sys.exit()."""

    def test_success_is_int(self):
        assert isinstance(EXIT_SUCCESS, int)

    def test_failure_is_int(self):
        assert isinstance(EXIT_FAILURE, int)

    def test_partial_is_int(self):
        assert isinstance(EXIT_PARTIAL, int)

    def test_precond_is_int(self):
        assert isinstance(EXIT_PRECOND, int)
