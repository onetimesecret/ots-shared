# packages/ots-shared/tests/ssh/test_env_network.py

"""Tests for the top-level ``network:`` block accessor in ots_shared.ssh.env.

The ``network:`` block carries the per-environment Hetzner private network
spec consumed by ``lots hcloud network ensure``. ``get_network`` is a
thin typed accessor: it validates types but does not validate semantics
(CIDR shape / zone membership) — that is the caller's job.
"""

from __future__ import annotations

import pytest

from ots_shared.ssh.env import MarkerNetwork, get_network


class TestGetNetworkHappyPath:
    """``get_network`` returns a MarkerNetwork for a valid block."""

    def test_returns_marker_network(self):
        marker = {
            "environment": "eu2",
            "network": {
                "name": "priv-net",
                "ip_range": "10.101.0.0/16",
                "network_zone": "eu-central",
            },
        }
        result = get_network(marker)
        assert isinstance(result, MarkerNetwork)
        assert result.name == "priv-net"
        assert result.ip_range == "10.101.0.0/16"
        assert result.network_zone == "eu-central"

    def test_marker_network_is_frozen(self):
        marker = {
            "network": {
                "name": "priv-net",
                "ip_range": "10.101.0.0/16",
                "network_zone": "eu-central",
            },
        }
        result = get_network(marker)
        assert result is not None
        # frozen=True dataclass — assignment must raise FrozenInstanceError
        # (a subclass of AttributeError).
        with pytest.raises(AttributeError):
            result.name = "other"  # type: ignore[misc]


class TestGetNetworkAbsent:
    """When the ``network:`` block is absent, ``get_network`` returns None."""

    def test_returns_none_when_block_absent(self):
        marker = {"environment": "eu2", "hosts": {"web": {}}}
        assert get_network(marker) is None

    def test_returns_none_for_empty_marker(self):
        assert get_network({}) is None

    def test_returns_none_when_network_is_none(self):
        # Explicit `network:` with no body parses as None in PyYAML —
        # treat the same as absent.
        marker = {"network": None}
        assert get_network(marker) is None


class TestGetNetworkBadTypes:
    """Type-level validation: wrong types fail loud."""

    def test_raises_when_block_is_not_a_dict(self):
        marker = {"network": "priv-net"}
        with pytest.raises((TypeError, ValueError)):
            get_network(marker)

    def test_raises_when_ip_range_is_int(self):
        marker = {
            "network": {
                "name": "priv-net",
                "ip_range": 5,  # int instead of "10.101.0.0/16"
                "network_zone": "eu-central",
            },
        }
        with pytest.raises((TypeError, ValueError)):
            get_network(marker)

    def test_raises_when_name_is_int(self):
        marker = {
            "network": {
                "name": 42,
                "ip_range": "10.101.0.0/16",
                "network_zone": "eu-central",
            },
        }
        with pytest.raises((TypeError, ValueError)):
            get_network(marker)

    def test_raises_when_network_zone_is_list(self):
        marker = {
            "network": {
                "name": "priv-net",
                "ip_range": "10.101.0.0/16",
                "network_zone": ["eu-central"],
            },
        }
        with pytest.raises((TypeError, ValueError)):
            get_network(marker)


class TestGetNetworkMissingKeys:
    """Required keys missing from the block must raise."""

    def test_raises_when_name_missing(self):
        marker = {
            "network": {
                "ip_range": "10.101.0.0/16",
                "network_zone": "eu-central",
            },
        }
        with pytest.raises((KeyError, ValueError)):
            get_network(marker)

    def test_raises_when_ip_range_missing(self):
        marker = {
            "network": {
                "name": "priv-net",
                "network_zone": "eu-central",
            },
        }
        with pytest.raises((KeyError, ValueError)):
            get_network(marker)

    def test_raises_when_network_zone_missing(self):
        marker = {
            "network": {
                "name": "priv-net",
                "ip_range": "10.101.0.0/16",
            },
        }
        with pytest.raises((KeyError, ValueError)):
            get_network(marker)

    def test_raises_when_block_is_empty_dict(self):
        marker = {"network": {}}
        with pytest.raises((KeyError, ValueError)):
            get_network(marker)
