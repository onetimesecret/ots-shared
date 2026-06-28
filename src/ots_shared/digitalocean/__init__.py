# src/ots_shared/digitalocean/__init__.py

"""Shared DigitalOcean library subset for OTS tools.

Mirrors :mod:`ots_shared.upcloud` but targets DigitalOcean (the official
``pydo`` SDK). Provider-specific divergences from Hetzner/UpCloud are documented
per module; the headline ones are: atomic regions (``region == zone``, no
``network_zone``), a flat VPC (one IP range, no subnets/routes), **no pinned
private IPs** (DO assigns them, so ``private_ip_*`` markers are fail-loud), a
64 KiB client-side cloud-init cap, no get-by-name (paginate + filter), and an
async-**action** model polled to terminal state.

``pydo`` is an OPTIONAL extra (multi-provider.md §6b) because it drags in the
Azure stack. Importing this package never requires it — :class:`Config` and the
error classifier import ``pydo`` / ``azure-core`` lazily — so a Hetzner-only
install loads it fine and only ``Config.get_client`` fails loud with an install
hint.

Public API:
    - Config: authenticated ``pydo.Client`` factory reading DIGITALOCEAN_TOKEN
      (preferred) or DIGITALOCEAN_ACCESS_TOKEN (alias). No project concept.
    - api_errors / classify: contextmanager + classifier mapping pydo/azure
      exceptions to friendly stderr messages and exit codes (1 API, 2 auth).
    - with_backoff / is_retryable: defensive exponential backoff (honoring
      Retry-After) for transient 429 / 5xx and network failures.
    - Regions: KNOWN_REGIONS, is_known_region, validate_region,
      regions_from_catalog.
    - wait_for_action / ProviderTimeout: bounded-backoff action polling.
    - Pagination: list_all, find_by_name (per_page=200, client-side filter).
    - Droplet defaults: MARKER_HOST_FIELDS, USER_DATA_LIMIT_BYTES (64 KiB),
      MarkerField, HostDefaults, CloudInitPayload, resolve_host_defaults,
      get_droplet_or_exit, load_cloud_init_user_data, marker_network_name,
      format_traffic.
    - Network plan: NetworkSpec, DesiredState, Action, parse_marker, diff_state
      — pure-logic flat-VPC reconciler.
"""

from .config import Config
from .droplet_defaults import (
    MARKER_HOST_FIELDS,
    USER_DATA_LIMIT_BYTES,
    CloudInitPayload,
    HostDefaults,
    MarkerField,
    format_traffic,
    get_droplet_or_exit,
    load_cloud_init_user_data,
    marker_network_name,
    resolve_host_defaults,
)
from .errors import api_errors, classify, is_retryable, with_backoff
from .network_plan import (
    Action,
    DesiredState,
    NetworkSpec,
    diff_state,
    parse_marker,
)
from .paginate import PER_PAGE_MAX, find_by_name, list_all
from .regions import (
    KNOWN_REGIONS,
    is_known_region,
    regions_from_catalog,
    validate_region,
)
from .wait import ProviderTimeout, wait_for_action

__all__ = [
    "KNOWN_REGIONS",
    "MARKER_HOST_FIELDS",
    "PER_PAGE_MAX",
    "USER_DATA_LIMIT_BYTES",
    "Action",
    "CloudInitPayload",
    "Config",
    "DesiredState",
    "HostDefaults",
    "MarkerField",
    "NetworkSpec",
    "ProviderTimeout",
    "api_errors",
    "classify",
    "diff_state",
    "find_by_name",
    "format_traffic",
    "get_droplet_or_exit",
    "is_known_region",
    "is_retryable",
    "list_all",
    "load_cloud_init_user_data",
    "marker_network_name",
    "parse_marker",
    "regions_from_catalog",
    "resolve_host_defaults",
    "validate_region",
    "wait_for_action",
    "with_backoff",
]
