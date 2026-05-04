# tests/ssh/test_env_get_host_ip.py

"""Contract tests for ``get_host_ip`` ordinal-aware lookup.

Five call sites in ``lots.cloudinit.cli`` and ``lots.confext.cli`` pass
three positional arguments. This file pins the signature so a future
refactor in ``ots-shared`` cannot silently revert to a 2-arg form
without these tests catching it.

The end-to-end behaviour (legacy scalar resolution, ordinal overrides,
fail-loud on missing data) is covered by
``packages/lots/tests/test_confext_ordinal.py`` against the live
resolver. This file is a thin signature-and-no-aliasing pin at the
``ots-shared`` boundary.
"""

from __future__ import annotations

from ots_shared.ssh.env import get_host_ip


class TestGetHostIpAcceptsOrdinal:
    """Pin the 3-arg signature so a refactor cannot regress to 2-arg."""

    def test_three_positional_args_does_not_raise_typeerror(self) -> None:
        marker = {
            "env_name": "eu",
            "hosts": {"web": {"private_ip_address": "10.101.1.11"}},
        }
        # A 2-arg signature would raise TypeError here; the test passing
        # is the contract: lots call sites can pass an ordinal.
        get_host_ip(marker, "web", "01")

    def test_ordinal_01_resolves_legacy_scalar(self) -> None:
        marker = {
            "env_name": "eu",
            "hosts": {"web": {"private_ip_address": "10.101.1.11"}},
        }
        assert get_host_ip(marker, "web", "01") == "10.101.1.11"

    def test_ordinal_override_wins_over_legacy_scalar(self) -> None:
        # Per the marker schema reference in
        # examples/environment/otsinfra.yaml: ordinals.<NN> overrides
        # the legacy scalar so each replica has its own IP.
        marker = {
            "env_name": "eu",
            "hosts": {
                "web": {
                    "private_ip_address": "10.101.1.11",
                    "ordinals": {
                        "02": {"private_ip_address": "10.101.1.99"},
                    },
                },
            },
        }
        assert get_host_ip(marker, "web", "01") == "10.101.1.11"
        assert get_host_ip(marker, "web", "02") == "10.101.1.99"


class TestGetHostIpDoesNotAlias:
    """The original silent wrong-IP bug: ordinal 02 receiving 01's IP.

    This is the load-bearing assertion — even if the signature pin
    above is satisfied, a resolver that maps every ordinal back to
    ``private_ip_address`` would re-introduce the bug.
    """

    def test_higher_ordinal_with_only_legacy_scalar_does_not_alias(self) -> None:
        marker = {
            "env_name": "eu",
            "hosts": {"web": {"private_ip_address": "10.101.1.11"}},
        }
        result = get_host_ip(marker, "web", "02")
        assert result != "10.101.1.11", (
            "ordinal '02' must not alias to the legacy scalar IP — "
            "that was the original silent wrong-IP bug that ordinal-aware "
            "resolution was added to prevent."
        )

    def test_unknown_role_does_not_return_a_sibling_ip(self) -> None:
        marker = {
            "env_name": "eu",
            "hosts": {"db": {"private_ip_address": "10.101.0.11"}},
        }
        result = get_host_ip(marker, "web", "01")
        assert result != "10.101.0.11"
