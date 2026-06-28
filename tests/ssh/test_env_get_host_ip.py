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


class TestGetHostIpSidecarFallback:
    """Step 4: the resolved-IP sidecar (DigitalOcean auto-assigned IPs).

    The marker carries NO ``private_ip_*`` (forbidden on DO §5.4a), so the
    assigned IP is recovered from ``.trust/resolved-ips.yaml`` keyed by the
    reconstructed ``<env>-<role>-<ordinal>`` hostname. All paths use
    ``tmp_path`` + an explicit ``marker_dir`` so no real ``.trust/`` is read.
    """

    def test_sidecar_resolves_when_marker_has_no_ip(self, tmp_path) -> None:
        from ots_shared.resolved_ip import write_resolved_ip

        write_resolved_ip("eu-web-01", "10.124.0.7", marker_dir=tmp_path)
        marker = {"env_name": "eu", "hosts": {"web": {"server_type": "s-1vcpu-1gb"}}}
        assert get_host_ip(marker, "web", "01", marker_dir=tmp_path) == "10.124.0.7"

    def test_sidecar_honours_ordinal_in_hostname_key(self, tmp_path) -> None:
        from ots_shared.resolved_ip import write_resolved_ip

        write_resolved_ip("eu-web-02", "10.124.0.8", marker_dir=tmp_path)
        marker = {"env_name": "eu", "hosts": {"web": {}}}
        assert get_host_ip(marker, "web", "02", marker_dir=tmp_path) == "10.124.0.8"
        # Ordinal 01 has no sidecar entry and no marker IP → None.
        assert get_host_ip(marker, "web", "01", marker_dir=tmp_path) is None

    def test_marker_pinned_ip_wins_over_sidecar(self, tmp_path) -> None:
        # Hetzner case: marker carries the pinned IP, so steps 1-3 return
        # first and the sidecar is never consulted (even if one exists).
        from ots_shared.resolved_ip import write_resolved_ip

        write_resolved_ip("eu-web-01", "10.124.0.7", marker_dir=tmp_path)
        marker = {"env_name": "eu", "hosts": {"web": {"private_ip_address": "10.101.1.11"}}}
        assert get_host_ip(marker, "web", "01", marker_dir=tmp_path) == "10.101.1.11"

    def test_no_env_name_skips_sidecar(self, tmp_path) -> None:
        # Cannot build the hostname key without env_name → return None,
        # never raise (no behaviour change for minimal markers).
        from ots_shared.resolved_ip import write_resolved_ip

        write_resolved_ip("eu-web-01", "10.124.0.7", marker_dir=tmp_path)
        marker = {"hosts": {"web": {}}}
        assert get_host_ip(marker, "web", "01", marker_dir=tmp_path) is None

    def test_no_sidecar_entry_returns_none(self, tmp_path) -> None:
        marker = {"env_name": "eu", "hosts": {"web": {}}}
        assert get_host_ip(marker, "web", "01", marker_dir=tmp_path) is None
