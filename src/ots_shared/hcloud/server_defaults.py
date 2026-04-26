# packages/ots-shared/src/ots_shared/hcloud/server_defaults.py

"""Library helpers for resolving Hetzner server defaults from .otsinfra.yaml.

The library subset of what used to live under
``lots.hcloud.commands.server._helpers``. CLI presentation helpers
(``print_*``) remain in lots. Diagnostic messages from
``load_cloud_init_user_data`` go to ``stderr`` so stdout stays clean
for ``--json`` callers.
"""

from __future__ import annotations

import gzip
import logging
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from hcloud import Client
from hcloud.servers.domain import Server

logger = logging.getLogger(__name__)

USER_DATA_LIMIT_BYTES = 32 * 1024


# ---------------------------------------------------------------------------
# Marker-backed flag table
# ---------------------------------------------------------------------------
#
# The single source of truth for which CLI flags on `server create` can be
# defaulted from `hosts.<role>` in .otsinfra.yaml. One row == one flag.
#
# To add a marker-backed flag:
#   1. Add a `MarkerField(...)` entry below with the marker key (as written
#      in YAML), the Python type it must coerce to, and — for sanity-checking
#      the user's YAML — the name of the CLI flag it backs.
#   2. In app.py's `create()`, pull the value out of the resolver's output
#      with `defaults.get("<marker_key>")` using the same precedence pattern
#      as the existing fields (explicit flag > marker > hardcoded default).
#
# No other files need to change. Unknown SCALAR/LIST-valued keys under
# `hosts.<role>` raise (fail-loud); unknown DICT-valued keys are silently
# ignored — these are foreign-tool sections (rots' `unce:`/`caddy:` file
# provisioning, etc.) and not ours to validate. Type mismatches on known
# keys always raise.

MarkerValue = str | list[str] | bool | int
MarkerKind = type[str] | type[list] | type[bool] | type[int]


@dataclass(frozen=True, slots=True)
class MarkerField:
    """One row in the marker allowlist.

    ``key`` is the YAML key under ``hosts.<role>``. ``kind`` is the Python
    type the value must match. For ``list``-kind fields, the internal
    representation is always ``list[str]`` — a YAML scalar is promoted to
    a one-element list; missing keys surface as ``[]``. This is deliberate
    (Lisp-style "everything is a list" for list-kind fields) so consumers
    never have to branch on ``str | list[str]``. ``flag`` is the CLI flag
    name used in error messages.
    """

    key: str
    kind: MarkerKind
    flag: str


# Order is stable for deterministic error messages listing allowed keys.
MARKER_HOST_FIELDS: tuple[MarkerField, ...] = (
    MarkerField("server_type", str, "--server-type"),
    MarkerField("image", str, "--image"),
    MarkerField("location", str, "--location"),
    MarkerField("private_ip_address", str, "--ip"),
    # Representative non-str examples proving the coercion story. Wiring
    # these into `create()` (if desired) is a separate pass.
    MarkerField("firewalls", list, "--firewall"),
    MarkerField("backup", bool, "--backup"),
)
# The network is defined once per environment under the top-level
# `network:` block (one network per env). Use marker_network_name() to
# read it; it is not a per-host field.

_MARKER_FIELDS_BY_KEY: dict[str, MarkerField] = {f.key: f for f in MARKER_HOST_FIELDS}


def _type_name(kind: MarkerKind) -> str:
    """Human label for error messages. Distinguishes list/bool/int/str."""
    match kind:
        case k if k is list:
            return "list[str]"
        case k if k is bool:
            return "bool"
        case k if k is int:
            return "int"
        case _:
            return "str"


def _coerce_marker_value(
    field: MarkerField, raw: object, *, marker_path: Path, role: str
) -> MarkerValue:
    """Validate and coerce a raw YAML value to the field's declared type.

    Raises SystemExit with a file/role/key/expected/got message on mismatch.
    YAML booleans must be real booleans (not ``"yes"``); YAML ints must be
    real ints (not ``"20"``). A scalar string under a list field is promoted
    to a one-element list so consumers see ``list[str]`` uniformly.
    """
    # bool is a subclass of int in Python — check it first so that `True`
    # under an int field is rejected loudly, not accepted.
    match field.kind:
        case k if k is bool:
            if isinstance(raw, bool):
                return raw
        case k if k is int:
            if isinstance(raw, bool):
                pass  # fall through to the error; bool is not int here
            elif isinstance(raw, int):
                return raw
        case k if k is str:
            if isinstance(raw, str) and raw:
                return raw
        case k if k is list:
            if isinstance(raw, list):
                if all(isinstance(item, str) and item for item in raw):
                    return list(raw)
            elif isinstance(raw, str) and raw:
                return [raw]

    got = type(raw).__name__
    raise SystemExit(
        f"{marker_path}: hosts.{role}.{field.key} "
        f"(used for {field.flag}) must be {_type_name(field.kind)}, got {got}: {raw!r}"
    )


@dataclass(frozen=True, slots=True)
class HostDefaults:
    """Typed defaults resolved from .otsinfra.yaml for a single host role.

    ``values`` maps marker key -> validated Python value. ``role`` is the
    resolved role key (explicit or auto-matched). ``marker_path`` is the
    YAML file the values came from, used for diagnostic messages.

    List-kind fields (see :data:`MARKER_HOST_FIELDS`) are **always** present
    in ``values`` — as ``[]`` when the YAML omits them, as ``[v]`` when YAML
    has a scalar, as ``[...]`` otherwise. Str/bool/int fields are only
    present when the YAML declares them (``get`` returns ``None``
    otherwise). Consumers of list-kind keys can therefore iterate without
    branching on ``None``.
    """

    values: dict[str, MarkerValue]
    role: str
    marker_path: Path

    def get(self, key: str) -> MarkerValue | None:
        """Return the coerced value for ``key`` or ``None`` if unset.

        For list-kind fields, the empty list ``[]`` is returned when YAML
        omits the key — not ``None``. Treat a ``None`` return as "not
        declared at all" (only possible for str/bool/int-kind fields).
        """
        return self.values.get(key)


def resolve_host_defaults(role: str | None, name: str) -> HostDefaults | None:
    """Resolve server create defaults from .otsinfra.yaml.

    Picks a host role either from an explicit ``role`` argument or — when
    unset — by tokenizing *name* on ``-`` and matching tokens against the
    marker's host keys.

    Returns ``None`` when no marker file is found on disk (i.e. the
    project has opted out of marker-backed defaults entirely).

    Raises ``SystemExit`` — with distinct messages — when:
      * the marker exists but has no ``hosts`` block,
      * an explicit ``--role`` does not match any entry,
      * auto-match finds zero or multiple role tokens in ``name``,
      * a value under ``hosts.<role>`` has the wrong type (known key),
      * ``hosts.<role>`` contains a scalar/list-valued key not in
        :data:`MARKER_HOST_FIELDS` (dict-valued keys are silently ignored
        as foreign-tool sections — rots' ``unce:``/``caddy:`` etc.).
    """
    try:
        from ots_shared.ssh.env import find_marker, load_marker
    except ImportError:
        # ots_shared.ssh is an optional dep; its absence is a packaging
        # bug, not something to silently paper over. But in ad-hoc use
        # (e.g. importing this module from a notebook without the sibling
        # package installed) we treat it the same as "no marker found".
        return None

    marker_path = find_marker()
    if marker_path is None:
        return None

    marker = load_marker(marker_path)
    if not isinstance(marker, dict) or "hosts" not in marker:
        raise SystemExit(
            f"{marker_path}: no 'hosts' block — cannot infer defaults. "
            f"Pass all of --server-type/--image/--location explicitly, or "
            f"add a hosts: section."
        )

    hosts = marker["hosts"]
    if not isinstance(hosts, dict) or not hosts:
        raise SystemExit(f"{marker_path}: 'hosts' block is empty — cannot infer defaults.")

    available = sorted(hosts.keys())
    resolved_role = _resolve_role(role, name, hosts, available, marker_path)

    host = hosts.get(resolved_role, {})
    if not isinstance(host, dict):
        raise SystemExit(
            f"{marker_path}: hosts.{resolved_role} must be a mapping, got {type(host).__name__}."
        )

    # Fail loud on unknown *scalar/list* keys (catches the typo case,
    # e.g. `server-type:` vs `server_type:`). Dict-valued keys are
    # foreign-tool sections (e.g. rots' `unce:`, `caddy:` file provisioning
    # blocks) and are silently ignored — marker schema is shared across
    # tools and not ours to validate.
    allowed = set(_MARKER_FIELDS_BY_KEY)
    unknown = sorted(k for k, v in host.items() if k not in allowed and not isinstance(v, dict))
    if unknown:
        raise SystemExit(
            f"{marker_path}: hosts.{resolved_role} has unknown key(s): "
            f"{unknown}. Allowed: {sorted(allowed)}. "
            f"(Dict-valued keys are ignored as foreign-tool sections.)"
        )

    # Seed list-kind fields with empty lists so consumers never see None
    # for a list-kind key. Str/bool/int-kind fields stay absent until YAML
    # declares them — None is their "not set" signal.
    values: dict[str, MarkerValue] = {f.key: [] for f in MARKER_HOST_FIELDS if f.kind is list}
    for key, raw in host.items():
        field = _MARKER_FIELDS_BY_KEY.get(key)
        if field is None:
            # Must be a dict value — skipped above.
            continue
        values[key] = _coerce_marker_value(field, raw, marker_path=marker_path, role=resolved_role)

    return HostDefaults(values=values, role=resolved_role, marker_path=marker_path)


def marker_network_name(marker_path: Path | None = None) -> str | None:
    """Return the top-level ``network.name`` from ``.otsinfra.yaml``.

    Walks up from cwd to find the marker (or uses ``marker_path`` if
    given). Returns ``None`` when no marker is found, when the marker
    has no ``network:`` block, or when the block has no ``name``. Raises
    ``SystemExit`` when the block is present but malformed (e.g. wrong
    types) — fail loud so the caller doesn't silently miss a typo.
    """
    try:
        from ots_shared.ssh.env import find_marker, get_network, load_marker
    except ImportError:
        return None

    if marker_path is None:
        marker_path = find_marker()
    if marker_path is None:
        return None

    marker = load_marker(marker_path)
    if not isinstance(marker, dict):
        return None

    try:
        network = get_network(marker)
    except TypeError as exc:
        raise SystemExit(f"{marker_path}: {exc}") from exc

    return network.name if network is not None else None


def _resolve_role(
    role: str | None,
    name: str,
    hosts: dict,
    available: list[str],
    marker_path: Path,
) -> str:
    """Explicit role wins; otherwise auto-match on hyphen-separated tokens."""
    if role is not None:
        if role not in hosts:
            raise SystemExit(
                f"{marker_path}: role '{role}' not declared in hosts. Available: {available}."
            )
        return role

    matches = [token for token in name.split("-") if token in hosts]
    if len(matches) == 1:
        resolved = matches[0]
        logger.info(
            "Inferred --role=%s from server name '%s' via %s",
            resolved,
            name,
            marker_path,
        )
        return resolved
    if len(matches) > 1:
        raise SystemExit(
            f"{marker_path}: server name '{name}' matches multiple roles "
            f"{matches}. Pass --role explicitly."
        )
    raise SystemExit(
        f"{marker_path}: server name '{name}' does not match any role. "
        f"Available: {available}. Pass --role explicitly or rename."
    )


def get_server_or_exit(client: Client, name: str) -> Server:
    """Look up server by name, exit with message if not found."""
    server = client.servers.get_by_name(name)
    if not server:
        raise SystemExit(f"Server '{name}' not found")
    return server


@dataclass(frozen=True)
class CloudInitPayload:
    """Cloud-init payload prepared for the Hetzner API.

    `user_data` is the string handed to hcloud.servers.create / rebuild.
    raw_size/payload_size are byte counts of the source YAML and the final
    field value respectively.

    Note on `gzipped`: the Hetzner Cloud API treats the user_data field as
    a plain YAML/text string and does NOT decode gzip before handing it to
    the instance metadata service. cloud-init on the target therefore sees
    the gzip bytes as literal text and fails to parse the cloud-config.
    The flag is retained for testing against providers that do preserve
    raw bytes end-to-end (AWS, OpenStack-style), but on Hetzner it should
    be left False. For payloads > 32 KiB on Hetzner, fetch externally via
    #include or runcmd curl from object storage instead.
    """

    user_data: str
    gzipped: bool
    raw_size: int
    payload_size: int

    @property
    def ratio(self) -> float:
        return self.raw_size / self.payload_size if self.payload_size else 0.0


def load_cloud_init_user_data(
    path: Path | None,
    cmd: str | None = None,
    gzip_compress: bool = False,
) -> CloudInitPayload | None:
    """Load cloud-init YAML from a file or shell command, optionally gzipped.

    Returns None when neither source is given. Raises SystemExit with a
    human-readable message for bad input, missing files, non-zero command
    exits, or payloads over the 32 KiB Hetzner limit.

    `gzip_compress` is opt-in and experimental: the Hetzner API stores and
    serves user_data as a plain string with no gzip awareness, so enabling
    it will almost certainly break cloud-init on Hetzner instances. Keep
    the default (False) unless you have verified that your target provider
    round-trips raw bytes through to the instance metadata service.
    """
    if path is not None and cmd is not None:
        raise SystemExit("--cloud-init and --cloud-init-cmd are mutually exclusive")

    if path is not None:
        if not path.exists():
            raise SystemExit(f"Cloud-init file not found: {path}")
        content = path.read_text()
        source = f"file: {path}"
    elif cmd is not None:
        print(f"Running cloud-init command: {cmd}", file=sys.stderr)
        proc = subprocess.run(
            cmd,
            shell=True,  # noqa: S602
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            if proc.stderr:
                print(proc.stderr, file=sys.stderr)
            raise SystemExit(f"Cloud-init command failed (exit {proc.returncode}): {cmd}")
        content = proc.stdout
        if not content.strip():
            raise SystemExit("Cloud-init command produced no output")
        source = "command"
    else:
        return None

    raw_bytes = content.encode("utf-8")
    raw_size = len(raw_bytes)

    if gzip_compress:
        compressed = gzip.compress(raw_bytes)
        user_data = compressed.decode("latin-1")
        payload_size = len(compressed)
        print(
            f"Loaded cloud-init from {source}; "
            f"gzipped {raw_size} -> {payload_size} bytes "
            f"({raw_size / payload_size:.1f}x)",
            file=sys.stderr,
        )
    else:
        user_data = content
        payload_size = raw_size
        print(f"Loaded cloud-init from {source} ({payload_size} bytes)", file=sys.stderr)

    if payload_size > USER_DATA_LIMIT_BYTES:
        hint = " even after gzip" if gzip_compress else "; try --gzip"
        raise SystemExit(f"Cloud-init payload exceeds 32 KiB limit ({payload_size} bytes){hint}")

    return CloudInitPayload(
        user_data=user_data,
        gzipped=gzip_compress,
        raw_size=raw_size,
        payload_size=payload_size,
    )


def format_traffic(bytes_val: int | None) -> str:
    """Format byte count as human-readable string."""
    if not bytes_val:
        return "0 B"
    val = float(bytes_val)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if val < 1024:
            return f"{val:.1f} {unit}"
        val /= 1024
    return f"{val:.1f} PB"
