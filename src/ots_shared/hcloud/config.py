# src/ots_shared/hcloud/config.py

"""Hetzner Cloud client configuration shared across OTS tools."""

import importlib.metadata
import os
from dataclasses import dataclass, field

from hcloud import Client


def _package_version() -> str:
    """Read package version from installed metadata, falling back to 0.0.0."""
    try:
        return importlib.metadata.version("ots-shared")
    except importlib.metadata.PackageNotFoundError:
        return "0.0.0"


@dataclass
class Config:
    """Hetzner Cloud CLI configuration.

    Reads from environment:
      HCLOUD_TOKEN       - API token (required for all operations)
      HCLOUD_PROJECT_ID  - Project identifier (for labeling/context)
    """

    project_id: str = field(default_factory=lambda: os.environ.get("HCLOUD_PROJECT_ID", ""))
    token: str = field(
        default_factory=lambda: os.environ.get("HCLOUD_TOKEN", ""),
        repr=False,
    )
    application_version: str = field(default_factory=_package_version)

    def get_client(self) -> Client:
        """Construct authenticated hcloud Client."""
        if not self.token:
            raise SystemExit("HCLOUD_TOKEN environment variable not set")
        return Client(
            token=self.token,
            application_name=f"hcloud-cli-{self.project_id}",
            application_version=self.application_version,
        )
