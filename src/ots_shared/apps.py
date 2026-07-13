# src/ots_shared/apps.py

"""Typed accessor for the env-level ``apps:`` block in ``otsinfra.yaml``.

The block declares which application containers an environment runs and
how many replicas of each unit type. It is env-level, not per-host —
ordinals are identical replicas, so every web host in the env renders
the same instance manifest::

    apps:
      onetimesecret:
        tag: v0.26.0-rc2      # concrete tag; alias tags rejected offline
        web_ports: [7043]
        workers: 1
        scheduler: true

``get_app_config`` distinguishes "feature not adopted" (no ``apps:`` key
at all — returns ``None``) from "adopted but broken" (key present but
malformed — raises :class:`AppConfigError`). There is no silent fallback
for the latter: a typo'd block must fail the push, not skip the render.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

__all__ = (
    "AppConfig",
    "AppConfigError",
    "get_app_config",
)

_APP_NAME = "onetimesecret"

# Concrete OCI image tag: docker/distribution reference grammar. Alias
# tags (``@current``) never match — they require registry resolution,
# which the offline ``rots instance render`` path forbids.
_TAG_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$")

_KNOWN_KEYS = frozenset({"tag", "web_ports", "workers", "scheduler"})


class AppConfigError(RuntimeError):
    """The marker's ``apps:`` block is present but malformed.

    Raised for missing/mistyped fields, empty ``web_ports``, and alias
    tags. Inherits ``RuntimeError`` (not ``ValueError``) for the same
    reason as ``HostnameError``: these are configuration problems in
    ``otsinfra.yaml``, not malformed inputs from a Python caller.
    """


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Validated ``apps.onetimesecret`` block.

    ``web_ports`` is a tuple so the config is hashable and cannot be
    mutated after validation.
    """

    tag: str
    web_ports: tuple[int, ...]
    workers: int = 1
    scheduler: bool = True


def _validate_tag(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise AppConfigError(
            f"apps.{_APP_NAME}.tag must be a non-empty str, got {type(value).__name__}: {value!r}"
        )
    if value.startswith("@"):
        raise AppConfigError(
            f"apps.{_APP_NAME}.tag {value!r} is an alias tag; offline render "
            "cannot resolve aliases — pin a concrete image tag (e.g. 'v0.26.0-rc2')"
        )
    if not _TAG_RE.match(value):
        raise AppConfigError(
            f"apps.{_APP_NAME}.tag {value!r} is not a valid image tag "
            "(allowed: [A-Za-z0-9_][A-Za-z0-9._-]*, max 128 chars)"
        )
    return value


def _validate_web_ports(value: Any) -> tuple[int, ...]:
    if not isinstance(value, list) or not value:
        raise AppConfigError(
            f"apps.{_APP_NAME}.web_ports must be a non-empty list of ints, "
            f"got {type(value).__name__}: {value!r}"
        )
    ports: list[int] = []
    for item in value:
        # bool is an int subclass; YAML `true` must not pass as a port.
        if isinstance(item, bool) or not isinstance(item, int):
            raise AppConfigError(
                f"apps.{_APP_NAME}.web_ports entries must be ints, "
                f"got {type(item).__name__}: {item!r}"
            )
        if not 1 <= item <= 65535:
            raise AppConfigError(
                f"apps.{_APP_NAME}.web_ports entry {item} is outside the valid port range 1-65535"
            )
        ports.append(item)
    if len(set(ports)) != len(ports):
        raise AppConfigError(f"apps.{_APP_NAME}.web_ports contains duplicates: {value!r}")
    return tuple(ports)


def _validate_workers(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise AppConfigError(
            f"apps.{_APP_NAME}.workers must be an int, got {type(value).__name__}: {value!r}"
        )
    if value < 0:
        raise AppConfigError(f"apps.{_APP_NAME}.workers must be >= 0, got {value}")
    return value


def _validate_scheduler(value: Any) -> bool:
    if not isinstance(value, bool):
        raise AppConfigError(
            f"apps.{_APP_NAME}.scheduler must be a bool, got {type(value).__name__}: {value!r}"
        )
    return value


def get_app_config(marker: dict) -> AppConfig | None:
    """Return the validated ``apps.onetimesecret`` block from *marker*.

    Returns ``None`` only when the top-level ``apps`` key is entirely
    absent (the environment has not adopted the feature). Once ``apps``
    exists, a missing or malformed ``onetimesecret`` entry raises
    :class:`AppConfigError` — never a silent skip.
    """
    if "apps" not in marker:
        return None

    apps = marker["apps"]
    if not isinstance(apps, dict):
        raise AppConfigError(f"'apps' must be a mapping, got {type(apps).__name__}: {apps!r}")
    if _APP_NAME not in apps:
        raise AppConfigError(
            f"'apps' block is present but has no '{_APP_NAME}' entry; "
            f"declared apps: {sorted(apps)!r}. Add 'apps.{_APP_NAME}' or "
            "remove the 'apps:' block entirely."
        )

    block = apps[_APP_NAME]
    if not isinstance(block, dict):
        raise AppConfigError(
            f"apps.{_APP_NAME} must be a mapping, got {type(block).__name__}: {block!r}"
        )

    unknown = sorted(set(block) - _KNOWN_KEYS)
    if unknown:
        raise AppConfigError(
            f"apps.{_APP_NAME} has unknown key(s) {unknown!r}; "
            f"allowed keys: {sorted(_KNOWN_KEYS)!r}"
        )
    for required in ("tag", "web_ports"):
        if required not in block:
            raise AppConfigError(f"apps.{_APP_NAME}.{required} is required but missing")

    kwargs: dict[str, Any] = {
        "tag": _validate_tag(block["tag"]),
        "web_ports": _validate_web_ports(block["web_ports"]),
    }
    if "workers" in block:
        kwargs["workers"] = _validate_workers(block["workers"])
    if "scheduler" in block:
        kwargs["scheduler"] = _validate_scheduler(block["scheduler"])
    return AppConfig(**kwargs)
