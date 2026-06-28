# src/ots_shared/digitalocean/config.py

"""DigitalOcean client configuration shared across OTS tools.

Mirrors :mod:`ots_shared.upcloud.config` but targets DigitalOcean's ``pydo``
SDK. Auth is a single personal-access-token path:

  * ``DIGITALOCEAN_TOKEN``         - preferred env var.
  * ``DIGITALOCEAN_ACCESS_TOKEN``  - accepted alias (the doctl / Terraform
    convention); ``DIGITALOCEAN_TOKEN`` wins when both are set.

There is **no project concept** that scopes auth (no ``HCLOUD_PROJECT_ID``
analogue) — a PAT is account-global. ``pydo`` drags in the Azure stack
(``azure-core`` et al.), so it is an **optional extra** (multi-provider.md
§6b): ``pip install 'ots-shared[digitalocean]'``. The import is therefore
lazy and inside :meth:`Config.get_client` — importing this module never
requires ``pydo``, so the package loads on a Hetzner-only install.
"""

from __future__ import annotations

import importlib.metadata
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pydo import Client

# Hint string reused by both the missing-token error and tests.
_ENV_HINT = "set DIGITALOCEAN_TOKEN (or DIGITALOCEAN_ACCESS_TOKEN)"


def _token_from_env() -> str:
    """Read the PAT, preferring DIGITALOCEAN_TOKEN over the alias."""
    return os.environ.get("DIGITALOCEAN_TOKEN") or os.environ.get("DIGITALOCEAN_ACCESS_TOKEN", "")


def _package_version() -> str:
    """Read package version from installed metadata, falling back to 0.0.0."""
    try:
        return importlib.metadata.version("ots-shared")
    except importlib.metadata.PackageNotFoundError:
        return "0.0.0"


@dataclass(frozen=True)
class Config:
    """DigitalOcean CLI configuration.

    Reads from environment:
      DIGITALOCEAN_TOKEN         - PAT (preferred)
      DIGITALOCEAN_ACCESS_TOKEN  - PAT (alias; used only when TOKEN is unset)

    Explicit constructor kwargs win over the environment so programmatic
    callers (tests, scripts) can override per-call.
    """

    token: str = field(default_factory=_token_from_env, repr=False)
    timeout: int = 120
    application_version: str = field(default_factory=_package_version)

    def get_client(self) -> Client:
        """Construct an authenticated ``pydo.Client``.

        Raises ``SystemExit`` with an env-var hint when no token is set, and a
        distinct install hint when the ``[digitalocean]`` extra is missing —
        never returns an un-authenticatable client.
        """
        try:
            from pydo import Client
        except ImportError as exc:  # pragma: no cover - packaging guard
            raise SystemExit(
                "pydo is not installed; install the DigitalOcean extra to use "
                "'lots do' commands (pip install 'ots-shared[digitalocean]')"
            ) from exc

        if not self.token:
            raise SystemExit(f"DigitalOcean credentials not set: {_ENV_HINT}")

        return Client(token=self.token, timeout=self.timeout)
