"""Canonical jurisdiction and environment vocabularies for OTS infrastructure.

These vocabularies are the single source of truth for taxonomy inference
across all OTS CLI tools (otsinfra, ots-cloudinit, hcloud-manager, etc.).

Jurisdictions are ISO 3166-1 alpha-2 country codes used in hostname
segments, directory structures, and inventory records.

Environments map common aliases to canonical short names, allowing
hostname segments like "stg" or "production" to resolve consistently.
"""

KNOWN_JURISDICTIONS: frozenset[str] = frozenset(
    {
        "ar",
        "at",
        "au",
        "be",
        "br",
        "ca",
        "ch",
        "cl",
        "co",
        "cz",
        "de",
        "dk",
        "es",
        "eu",
        "fi",
        "fr",
        "gb",
        "hk",
        "hu",
        "ie",
        "in",
        "it",
        "jp",
        "kr",
        "mx",
        "nl",
        "no",
        "nz",
        "pe",
        "pl",
        "pt",
        "ro",
        "se",
        "sg",
        "tw",
        "uk",
        "us",
        "za",
    }
)
"""ISO 3166-1 alpha-2 country codes used as jurisdiction identifiers.

Checked against hostname segments split on '-' for taxonomy inference.
"""

KNOWN_ROLES: frozenset[str] = frozenset(
    {
        "web",
        "db",
        "redis",
        "jump",
        "bastion",
        "worker",
        "monitor",
        "proxy",
        "mail",
        "dns",
    }
)
"""Common host roles in OTS infrastructure.

Used for validation warnings in otsinfra host add/edit. Not an exhaustive
list -- unknown roles produce a warning, not an error.
"""

KNOWN_ENVIRONMENTS: dict[str, str] = {
    "prod": "prod",
    "production": "prod",
    "staging": "staging",
    "stg": "staging",
    "stage": "staging",
    "dev": "dev",
    "development": "dev",
    "test": "test",
    "testing": "test",
    "qa": "qa",
    "uat": "uat",
    "demo": "demo",
    "sandbox": "sandbox",
    "lab": "lab",
}
"""Map of environment aliases to canonical short names.

Keys are the recognized input strings (from hostnames, CLI flags, etc.).
Values are the canonical names stored in inventory records.
"""
