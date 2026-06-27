# src/ots_shared/upcloud/__init__.py

"""Shared UpCloud library subset for OTS tools.

Mirrors :mod:`ots_shared.hcloud` but targets UpCloud (``upcloud-api``
2.9.0). Provider-specific divergences from Hetzner are documented per
module; the headline ones are: atomic zones (``region == zone``, no
``network_zone``), one IP range per network, no client-side cloud-init size
cap, action-less state polling, and an append-only firewall model.

Public API:
    - Config: authenticated CloudManager factory reading UPCLOUD_TOKEN
      (preferred) or UPCLOUD_USERNAME + UPCLOUD_PASSWORD from the
      environment. No project concept.
    - api_errors: contextmanager mapping UpCloud exceptions to friendly
      stderr messages and exit codes (1 API, 2 config/auth).
    - with_backoff / is_retryable: defensive exponential backoff for
      transient 429/5xx and network timeouts (the SDK has none).
    - Zones: KNOWN_ZONES, is_known_zone, validate_zone, zones_from_catalog.
    - wait_for_state / wait_for_storage_state: action-less state polling.
    - Server defaults: MARKER_HOST_FIELDS, USER_DATA_LIMIT_BYTES (None),
      MarkerField, HostDefaults, CloudInitPayload, resolve_host_defaults,
      marker_network_name, get_server_or_exit, load_cloud_init_user_data,
      format_traffic.
    - Network plan: NetworkSpec, DesiredState, Action, parse_marker,
      diff_state — pure-logic single-range reconciler.
"""

from .config import Config
from .errors import api_errors, is_retryable, with_backoff
from .network_plan import (
    Action,
    DesiredState,
    NetworkSpec,
    diff_state,
    parse_marker,
)
from .server_defaults import (
    MARKER_HOST_FIELDS,
    USER_DATA_LIMIT_BYTES,
    CloudInitPayload,
    HostDefaults,
    MarkerField,
    format_traffic,
    get_server_or_exit,
    load_cloud_init_user_data,
    marker_network_name,
    resolve_host_defaults,
)
from .wait import wait_for_state, wait_for_storage_state
from .zones import KNOWN_ZONES, is_known_zone, validate_zone, zones_from_catalog

__all__ = [
    "KNOWN_ZONES",
    "MARKER_HOST_FIELDS",
    "USER_DATA_LIMIT_BYTES",
    "Action",
    "CloudInitPayload",
    "Config",
    "DesiredState",
    "HostDefaults",
    "MarkerField",
    "NetworkSpec",
    "api_errors",
    "diff_state",
    "format_traffic",
    "get_server_or_exit",
    "is_known_zone",
    "is_retryable",
    "load_cloud_init_user_data",
    "marker_network_name",
    "parse_marker",
    "resolve_host_defaults",
    "validate_zone",
    "wait_for_state",
    "wait_for_storage_state",
    "with_backoff",
    "zones_from_catalog",
]
