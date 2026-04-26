# packages/ots-shared/src/ots_shared/hcloud/__init__.py

"""Shared Hetzner Cloud library subset for OTS tools.

Public API:
    - Config: authenticated hcloud Client factory reading HCLOUD_TOKEN /
      HCLOUD_PROJECT_ID from the environment.
    - api_errors: contextmanager that maps hcloud exceptions to friendly
      stderr messages and exits.
    - Zones: KNOWN_ZONES, LOCATION_TO_ZONE — Hetzner network zones and the
      location -> zone mapping used by the `network ensure` reconciler.
    - Network plan: NetworkSpec, SubnetSpec, DesiredState, Action,
      parse_marker, diff_state — pure-logic reconciler driving
      `lots hcloud network ensure`.
    - Server defaults: MarkerField, MARKER_HOST_FIELDS, HostDefaults,
      CloudInitPayload, resolve_host_defaults, marker_network_name,
      get_server_or_exit, load_cloud_init_user_data, format_traffic —
      shared pieces of `lots hcloud server create` that other tools may
      reuse (no CLI output here; that lives in lots).
"""

from .config import Config
from .errors import api_errors
from .network_plan import (
    Action,
    DesiredState,
    NetworkSpec,
    SubnetSpec,
    diff_state,
    parse_marker,
)
from .server_defaults import (
    MARKER_HOST_FIELDS,
    CloudInitPayload,
    HostDefaults,
    MarkerField,
    format_traffic,
    get_server_or_exit,
    load_cloud_init_user_data,
    marker_network_name,
    resolve_host_defaults,
)
from .zones import KNOWN_ZONES, LOCATION_TO_ZONE

__all__ = [
    "KNOWN_ZONES",
    "LOCATION_TO_ZONE",
    "MARKER_HOST_FIELDS",
    "Action",
    "CloudInitPayload",
    "Config",
    "DesiredState",
    "HostDefaults",
    "MarkerField",
    "NetworkSpec",
    "SubnetSpec",
    "api_errors",
    "diff_state",
    "format_traffic",
    "get_server_or_exit",
    "load_cloud_init_user_data",
    "marker_network_name",
    "parse_marker",
    "resolve_host_defaults",
]
