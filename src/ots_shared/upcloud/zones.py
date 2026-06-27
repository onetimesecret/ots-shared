# src/ots_shared/upcloud/zones.py

"""UpCloud zone constants and validation.

UpCloud zones are **atomic**: ``region == zone``. There is **no
network-zone grouping** (the Hetzner ``LOCATION_TO_ZONE`` concept does not
exist here) and ``Substrate.network_zone`` is always ``None`` for UpCloud.
A network and the servers attached to it must live in the same atomic
zone; a wrong zone fails loud server-side regardless.

:data:`KNOWN_ZONES` exists as a single source of truth so the
``network ensure`` reconciler can validate ``otsinfra.yaml`` and offer
tab-completion without an API call. Treat it as a hint, not a closed set:
new public zones appear over time and a live ``get_zones()`` enumeration is
authoritative. :func:`validate_zone` therefore does **not** reject unknown
ids by default — it only flags them.
"""

from __future__ import annotations

# Representative public UpCloud zone ids. Verified against the documented
# public DC list; a live get_zones() call is authoritative at runtime.
KNOWN_ZONES: frozenset[str] = frozenset(
    {
        "fi-hel1",
        "fi-hel2",
        "de-fra1",
        "nl-ams1",
        "uk-lon1",
        "us-nyc1",
        "us-chi1",
        "us-sjo1",
        "sg-sin1",
        "au-syd1",
        "es-mad1",
        "pl-waw1",
        "se-sto1",
    }
)


def is_known_zone(zone: str) -> bool:
    """Return True if ``zone`` is in the offline :data:`KNOWN_ZONES` set."""
    return zone in KNOWN_ZONES


def validate_zone(zone: object) -> str:
    """Validate a zone id and return it normalized.

    Requires a non-empty string (UpCloud zone ids look like ``de-fra1``).
    Raises ``SystemExit`` on a missing/empty/non-string value — fail loud
    rather than letting a bad zone reach the API. Unknown-but-well-formed
    ids are accepted (the zone catalog grows); the server-side call is the
    final authority.
    """
    if not isinstance(zone, str) or not zone:
        raise SystemExit(f"zone must be a non-empty UpCloud zone id (e.g. de-fra1), got {zone!r}")
    return zone


def zones_from_catalog(payload: dict) -> list[str]:
    """Unwrap the raw ``get_zones()`` envelope into a list of zone ids.

    ``get_zones()`` returns ``{"zones": {"zone": [{"id", "description",
    "public"}, ...]}}`` (not wrapped into objects). Returns the ``id`` of
    every entry; tolerates a missing/short envelope by returning ``[]``.
    """
    zones = payload.get("zones", {}) if isinstance(payload, dict) else {}
    entries = zones.get("zone", []) if isinstance(zones, dict) else []
    return [z["id"] for z in entries if isinstance(z, dict) and "id" in z]
