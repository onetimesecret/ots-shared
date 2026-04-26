# src/ots_shared/ssh/hostname.py

"""Strict <env>-<role>-<ordinal?> hostname parser, marker-validated.

Single source of truth for ``hostname → (env, role, ordinal)`` resolution
across the lots/ots-shared monorepo. Replaces four divergent
implementations that mixed ``rsplit('-', 2)`` (provision/confext CLIs)
with ``split('-')`` token-match (deploy substrate, hcloud server
defaults). The two algorithms disagreed on hostnames like
``eu-db-test-01`` — this module pins the contract so every call site
agrees.

Contract (USER-PINNED, see issue #59 follow-on):

* The marker is **required**. ``parse_hostname`` does not invent
  defaults when the marker is missing or malformed — callers that
  don't have a marker on disk must fail loud at the discovery layer
  before reaching this module.
* ``marker['env_name']`` is **required** and non-empty. Missing →
  :class:`MarkerEnvNameMissing`.
* The parsed env prefix must equal ``marker['env_name']``. Mismatch
  → :class:`HostnameEnvMismatch`. Empty env prefix (e.g. ``db-01``)
  → :class:`HostnameEmptyEnv`.
* The role is selected by walking the longest-to-shortest suffix of
  the post-ordinal remainder against ``marker['hosts']`` — first hit
  wins. No suffix matches → :class:`HostnameNoRoleMatch`.
* The ordinal is the trailing pure-digit segment if present;
  otherwise it defaults to ``"01"`` (the only silent default in the
  module — chosen because operators write ``lots deploy eu-db`` to
  mean ``eu-db-01`` and have done so since the cloudinit defaults
  landed). Every other failure surfaces as a typed exception.

The error hierarchy roots at :class:`HostnameError` so callers can
catch broadly while still matching the specific failure for
operator-facing diagnostics.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = (
    "ParsedHostname",
    "parse_hostname",
    "HostnameError",
    "HostnameNoRoleMatch",
    "HostnameEmptyEnv",
    "HostnameEnvMismatch",
    "MarkerEnvNameMissing",
    "EnvNameConflict",
)


# ---------------------------------------------------------------------------
# Typed errors
# ---------------------------------------------------------------------------


class HostnameError(RuntimeError):
    """Base for hostname parser failures.

    All concrete failures inherit from this so a caller can ``except
    HostnameError`` to catch the lot. Inherits ``RuntimeError`` (not
    ``ValueError``) because the failures are configuration/environment
    problems, not malformed inputs from a Python caller.
    """


# N818 (Error-suffix convention) is suppressed below: the contract for
# this module pins these exact class names so callers across the
# monorepo can match them by the names spelled out in docs/specs.
# Renaming to add an ``Error`` suffix would break that contract.


class HostnameNoRoleMatch(HostnameError):  # noqa: N818
    """No suffix of the hostname matches a key in ``marker['hosts']``."""


class HostnameEmptyEnv(HostnameError):  # noqa: N818
    """The env prefix is empty (e.g. ``db-01`` against ``hosts={db}``)."""


class HostnameEnvMismatch(HostnameError):  # noqa: N818
    """The parsed env does not equal ``marker['env_name']``."""


class MarkerEnvNameMissing(HostnameError):  # noqa: N818
    """The marker has no ``env_name`` field, or it is empty."""


class EnvNameConflict(HostnameError):  # noqa: N818
    """``$ENV_NAME`` is set and disagrees with ``marker['env_name']``."""


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ParsedHostname:
    """Parsed components of a ``<env>-<role>-<ordinal?>`` hostname.

    All three fields are non-empty strings. ``ordinal`` is a digit string
    (preserves leading zeros — ``"01"``, not ``1``) so callers can use it
    directly for image-name composition without re-formatting.
    """

    env: str
    role: str
    ordinal: str


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def parse_hostname(hostname: str, marker: dict[str, Any]) -> ParsedHostname:
    """Parse ``hostname`` against ``marker`` into env/role/ordinal.

    Algorithm:

    1. If the trailing ``-``-segment is all digits, that is the ordinal.
       Otherwise ordinal defaults to ``"01"``.
    2. Generate suffix candidates of the post-ordinal remainder,
       longest-to-shortest (consecutive trailing tokens joined with
       ``-``). The first candidate present as a key in
       ``marker['hosts']`` wins → that is the role; everything before
       (with the trailing ``-`` stripped) is the env prefix.
    3. Validate: env equals ``marker['env_name']``, env is non-empty,
       role exists in ``marker['hosts']``.

    The marker is **required**. Missing ``env_name`` →
    :class:`MarkerEnvNameMissing`. Empty env prefix →
    :class:`HostnameEmptyEnv`. No suffix matches →
    :class:`HostnameNoRoleMatch`. Env prefix differs from
    ``marker['env_name']`` → :class:`HostnameEnvMismatch`.
    """
    if not isinstance(hostname, str) or not hostname:
        raise HostnameError(f"hostname must be a non-empty str, got {hostname!r}")

    expected_env = _require_env_name(marker)
    hosts = _require_hosts(marker)

    tokens = hostname.split("-")
    if not tokens or any(t == "" for t in tokens):
        # Either the input had a leading/trailing/double hyphen.
        raise HostnameError(
            f"hostname {hostname!r} has empty hyphen-separated segments; "
            "expected <env>-<role>[-<ordinal>]"
        )

    # 1. Ordinal: trailing pure-digit segment, or default "01".
    if len(tokens) >= 2 and tokens[-1].isdigit():
        ordinal = tokens[-1]
        remainder = tokens[:-1]
    else:
        ordinal = "01"
        remainder = tokens

    if not remainder:
        raise HostnameError(
            f"hostname {hostname!r} has no env/role segments after stripping ordinal"
        )

    # 2. Longest-to-shortest suffix match against hosts keys.
    role: str | None = None
    env_tokens: list[str] = []
    for split_at in range(len(remainder)):
        candidate = _join(remainder[split_at:])
        if candidate in hosts:
            role = candidate
            env_tokens = remainder[:split_at]
            break

    if role is None:
        tried = [_join(remainder[i:]) for i in range(len(remainder))]
        raise HostnameNoRoleMatch(
            f"hostname {hostname!r} matches no role in marker hosts "
            f"({sorted(hosts)}); tried suffixes {tried}"
        )

    env = _join(env_tokens)

    # 3. Validate env prefix.
    if not env:
        raise HostnameEmptyEnv(
            f"hostname {hostname!r} has empty env prefix; "
            f"expected {expected_env!r}-{role}-<ordinal>"
        )

    if env != expected_env:
        raise HostnameEnvMismatch(
            f"hostname {hostname!r} env prefix {env!r} does not match "
            f"marker env_name {expected_env!r}"
        )

    return ParsedHostname(env=env, role=role, ordinal=ordinal)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _join(tokens: list[str]) -> str:
    """``-`` joiner pulled out so we can call it inside f-string contexts.

    PEP 701 (multi-line / quote-reuse f-strings) is 3.12+; ots-shared
    targets 3.11. Hoisting the join keeps f-strings simple.
    """
    return "-".join(tokens)


def _require_env_name(marker: dict[str, Any]) -> str:
    """Pull the resolved env name via :func:`resolve_env_name`.

    Delegates to ``ots_shared.ssh.env.resolve_env_name`` so the same
    precedence rules (marker is source of truth; ``$ENV_NAME`` cross-
    checks via :class:`EnvNameConflict`) apply uniformly to every
    hostname-resolving call site. The lazy import breaks the
    ``env`` ↔ ``hostname`` cycle: ``env`` imports the error types from
    this module at top level, and this function imports back lazily
    only when invoked.
    """
    from ots_shared.ssh.env import resolve_env_name

    return resolve_env_name(marker)


def _require_hosts(marker: dict[str, Any]) -> dict[str, Any]:
    """Pull ``hosts`` out of *marker*, fail loud if absent or empty."""
    hosts = marker.get("hosts") if isinstance(marker, dict) else None
    if not isinstance(hosts, dict) or not hosts:
        raise HostnameNoRoleMatch("marker has no non-empty 'hosts' block; cannot resolve role")
    return hosts
