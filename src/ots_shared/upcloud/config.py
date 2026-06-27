# src/ots_shared/upcloud/config.py

"""UpCloud client configuration shared across OTS tools.

Mirrors :mod:`ots_shared.hcloud.config` but targets UpCloud's
``CloudManager`` god-object. Auth has two mutually-exclusive paths:

  * ``UPCLOUD_TOKEN``    - ``ucat_`` Bearer token (preferred; >= 2.8.0).
  * ``UPCLOUD_USERNAME`` + ``UPCLOUD_PASSWORD`` - HTTP Basic fallback.

Token wins when both are present. There is **no project concept** on
UpCloud (no ``HCLOUD_PROJECT_ID`` analogue) — auth is account-global.

We read the environment explicitly rather than relying on
``Credentials.parse`` (which falls back to the system keyring) so a stray
keyring entry can never silently authenticate. Fail loud (``SystemExit``)
when neither auth path is configured.
"""

from __future__ import annotations

import importlib.metadata
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from upcloud_api import CloudManager

# Hint string reused by both the missing-creds error and tests.
_ENV_HINT = "set UPCLOUD_TOKEN (preferred) or UPCLOUD_USERNAME + UPCLOUD_PASSWORD"


def _package_version() -> str:
    """Read package version from installed metadata, falling back to 0.0.0."""
    try:
        return importlib.metadata.version("ots-shared")
    except importlib.metadata.PackageNotFoundError:
        return "0.0.0"


@dataclass(frozen=True)
class Config:
    """UpCloud CLI configuration.

    Reads from environment:
      UPCLOUD_TOKEN      - ``ucat_`` Bearer token (preferred)
      UPCLOUD_USERNAME   - HTTP Basic username (fallback, with password)
      UPCLOUD_PASSWORD   - HTTP Basic password (fallback, with username)

    Explicit constructor kwargs win over the environment so programmatic
    callers (tests, scripts) can override per-call. The token takes
    precedence over username/password whenever it is non-empty.
    """

    token: str = field(
        default_factory=lambda: os.environ.get("UPCLOUD_TOKEN", ""),
        repr=False,
    )
    username: str = field(default_factory=lambda: os.environ.get("UPCLOUD_USERNAME", ""))
    password: str = field(
        default_factory=lambda: os.environ.get("UPCLOUD_PASSWORD", ""),
        repr=False,
    )
    timeout: int = 60
    application_version: str = field(default_factory=_package_version)

    def get_client(self) -> CloudManager:
        """Construct an authenticated UpCloud ``CloudManager``.

        Resolution order: a non-empty ``token`` builds a Bearer client;
        otherwise both ``username`` and ``password`` build a Basic client.
        Raises ``SystemExit`` with an env-var hint when neither path is
        satisfied — never returns an un-authenticatable client.
        """
        try:
            from upcloud_api import CloudManager
        except ImportError as exc:  # pragma: no cover - packaging guard
            raise SystemExit(
                "upcloud-api is not installed; install it to use UpCloud commands "
                "(pip install 'upcloud-api>=2.9')"
            ) from exc

        kwargs: dict[str, Any] = {"timeout": self.timeout}
        if self.token:
            kwargs["token"] = self.token
        elif self.username and self.password:
            kwargs["username"] = self.username
            kwargs["password"] = self.password
        else:
            raise SystemExit(f"UpCloud credentials not set: {_ENV_HINT}")

        return CloudManager(**kwargs)
