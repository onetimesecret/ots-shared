# src/ots_shared/hcloud/zones.py

"""Hetzner Cloud network zone constants and location-to-zone mapping.

A Hetzner Network is anchored in exactly one network zone, and each
subnet must belong to that same zone. Servers are deployed in
*locations* (e.g. ``nbg1``, ``fsn1``); the location's zone must match
the network's zone or attaching the server to the network fails.

These constants exist as a single source of truth so the
``network ensure`` reconciler can validate ``otsinfra.yaml`` without
making an API call.
"""

from __future__ import annotations

KNOWN_ZONES: frozenset[str] = frozenset(
    {
        "eu-central",
        "us-east",
        "us-west",
        "ap-southeast",
    }
)

LOCATION_TO_ZONE: dict[str, str] = {
    "nbg1": "eu-central",
    "fsn1": "eu-central",
    "hel1": "eu-central",
    "ash": "us-east",
    "hil": "us-west",
    "sin": "ap-southeast",
}
