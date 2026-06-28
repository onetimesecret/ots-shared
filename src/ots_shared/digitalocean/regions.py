# src/ots_shared/digitalocean/regions.py

"""DigitalOcean region constants and validation.

DigitalOcean regions are **atomic**: ``region == zone``. There is **no
network-zone grouping** (the Hetzner ``LOCATION_TO_ZONE`` concept does not
exist here) and ``Substrate.network_zone`` is always ``None`` for DO. A droplet,
its VPC, and its volumes must live in the same region; a wrong region fails loud
server-side regardless.

:data:`KNOWN_REGIONS` exists as a single source of truth so the substrate
validator and tab-completion work without an API call. Treat it as a hint, not a
closed set: DO opens new regions over time and a live ``regions.list``
enumeration is authoritative. :func:`validate_region` therefore does **not**
reject unknown-but-well-formed ids — it only flags them.
"""

from __future__ import annotations

# Representative public DigitalOcean region slugs. A live regions.list() call is
# authoritative at runtime; this set is an offline hint that grows over time.
KNOWN_REGIONS: frozenset[str] = frozenset(
    {
        "nyc1",
        "nyc2",
        "nyc3",
        "ams3",
        "sfo2",
        "sfo3",
        "sgp1",
        "lon1",
        "fra1",
        "tor1",
        "blr1",
        "syd1",
    }
)


def is_known_region(region: str) -> bool:
    """Return True if ``region`` is in the offline :data:`KNOWN_REGIONS` set."""
    return region in KNOWN_REGIONS


def validate_region(region: object) -> str:
    """Validate a region slug and return it normalized.

    Requires a non-empty string (DO region slugs look like ``nyc3``). Raises
    ``SystemExit`` on a missing/empty/non-string value — fail loud rather than
    letting a bad region reach the API. Unknown-but-well-formed slugs are
    accepted (the region catalog grows); the server-side call is the final
    authority.
    """
    if not isinstance(region, str) or not region:
        raise SystemExit(
            f"region must be a non-empty DigitalOcean region slug (e.g. nyc3), got {region!r}"
        )
    return region


def regions_from_catalog(payload: dict) -> list[str]:
    """Unwrap the raw ``regions.list()`` envelope into a list of region slugs.

    ``regions.list()`` returns ``{"regions": [{"slug", "name", "available",
    ...}], ...}``. Returns the ``slug`` of every entry; tolerates a
    missing/short envelope by returning ``[]``.
    """
    regions = payload.get("regions", []) if isinstance(payload, dict) else []
    return [r["slug"] for r in regions if isinstance(r, dict) and "slug" in r]
