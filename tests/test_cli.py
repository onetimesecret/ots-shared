"""Tests for ots_shared.cli — shared cyclopts type aliases."""

import typing

import cyclopts

from ots_shared.cli import DryRun, Follow, JsonOutput, Lines, Quiet, Yes


def _get_cyclopts_param(alias):
    """Extract the cyclopts.Parameter metadata from an Annotated type."""
    for m in typing.get_args(alias):
        if isinstance(m, cyclopts.Parameter):
            return m
    return None


class TestAliasBaseTypes:
    """Each alias should wrap the expected base type."""

    def test_quiet_is_bool(self):
        assert typing.get_args(Quiet)[0] is bool

    def test_dry_run_is_bool(self):
        assert typing.get_args(DryRun)[0] is bool

    def test_yes_is_bool(self):
        assert typing.get_args(Yes)[0] is bool

    def test_follow_is_bool(self):
        assert typing.get_args(Follow)[0] is bool

    def test_lines_is_int(self):
        assert typing.get_args(Lines)[0] is int

    def test_json_output_is_bool(self):
        assert typing.get_args(JsonOutput)[0] is bool


class TestAliasLongFlags:
    """Each alias should have the correct long flag name."""

    def test_quiet_long_flag(self):
        p = _get_cyclopts_param(Quiet)
        assert "--quiet" in p.name

    def test_dry_run_long_flag(self):
        p = _get_cyclopts_param(DryRun)
        assert "--dry-run" in p.name

    def test_yes_long_flag(self):
        p = _get_cyclopts_param(Yes)
        assert "--yes" in p.name

    def test_follow_long_flag(self):
        p = _get_cyclopts_param(Follow)
        assert "--follow" in p.name

    def test_lines_long_flag(self):
        p = _get_cyclopts_param(Lines)
        assert "--lines" in p.name

    def test_json_output_long_flag(self):
        p = _get_cyclopts_param(JsonOutput)
        assert "--json" in p.name


class TestAliasShortFlags:
    """Each alias should have the correct short flag."""

    def test_quiet_short_flag(self):
        p = _get_cyclopts_param(Quiet)
        assert "-q" in p.name

    def test_dry_run_short_flag(self):
        p = _get_cyclopts_param(DryRun)
        assert "-n" in p.name

    def test_yes_short_flag(self):
        p = _get_cyclopts_param(Yes)
        assert "-y" in p.name

    def test_follow_short_flag(self):
        p = _get_cyclopts_param(Follow)
        assert "-f" in p.name

    def test_lines_short_flag(self):
        p = _get_cyclopts_param(Lines)
        assert "-l" in p.name

    def test_json_output_short_flag(self):
        p = _get_cyclopts_param(JsonOutput)
        assert "-j" in p.name


class TestDryRunNegativeDisabled:
    """DryRun should not generate --no-dry-run."""

    def test_dry_run_negative_empty(self):
        p = _get_cyclopts_param(DryRun)
        assert len(p.negative) == 0


class TestAliasHelpStrings:
    """Each alias should have a non-empty help string."""

    def test_quiet_has_help(self):
        p = _get_cyclopts_param(Quiet)
        assert p.help

    def test_dry_run_has_help(self):
        p = _get_cyclopts_param(DryRun)
        assert p.help

    def test_yes_has_help(self):
        p = _get_cyclopts_param(Yes)
        assert p.help

    def test_follow_has_help(self):
        p = _get_cyclopts_param(Follow)
        assert p.help

    def test_lines_has_help(self):
        p = _get_cyclopts_param(Lines)
        assert p.help

    def test_json_output_has_help(self):
        p = _get_cyclopts_param(JsonOutput)
        assert p.help
