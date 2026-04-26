# src/ots_shared/init.py

"""Shared ``init`` sub-app for OTS CLI tools.

Creates the ``.otsinfra.yaml`` marker file that signals "this is an
OTS environment directory" to all OTS tools (lots, pots, rots), plus
the surrounding scaffold (``.gitignore``, ``.envrc`` template, and the
``.trust/`` material).

Usage in a tool's ``cli.py``::

    from ots_shared.init import app as init_app
    root_app.command(init_app)
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import cyclopts

from ots_shared.ssh.env import (
    DEFAULT_HOSTS,
    create_envrc_template,
    create_gitignore,
    create_marker,
    load_marker,
)
from ots_shared.trust.init_step import create_trust_material

app = cyclopts.App(
    name="init",
    help="Initialize an OTS environment directory.",
)


@app.default
def init(
    environment: Annotated[
        str | None,
        cyclopts.Parameter(help="Environment name (default: directory name)"),
    ] = None,
    *,
    directory: Annotated[
        Path,
        cyclopts.Parameter(
            name=["--directory", "-C"],
            help="Target directory (default: current directory)",
        ),
    ] = Path("."),
    force: Annotated[
        bool,
        cyclopts.Parameter(
            help=(
                "Overwrite existing init files "
                "(.otsinfra.yaml, .gitignore, .envrc template, .trust/)"
            )
        ),
    ] = False,
    ca_days: Annotated[
        int,
        cyclopts.Parameter(
            name=["--ca-days"],
            help="Validity period in days for the local CA (default: 1460 / 4 years)",
        ),
    ] = 1460,
    leaf_days: Annotated[
        int,
        cyclopts.Parameter(
            name=["--leaf-days"],
            help="Validity period in days for per-host TLS leaves (default: 730 / 24 months)",
        ),
    ] = 730,
) -> None:
    """Create .otsinfra.yaml environment marker and supporting scaffold.

    The marker signals to lots, pots, and rots that the directory is
    an OTS environment. Direnv handles env vars; this file carries
    structured metadata (environment name, creation date).

    When no environment name is given, uses the directory name.

    Examples:
        lots init              # uses current directory name
        pots init              # same command, same result
        lots init eu2
        pots init -C ~/ops/environments/non-prod/eu2
    """
    target = directory.resolve()
    environment = environment or target.name or "default"
    if not target.is_dir():
        print(f"Error: {target} is not a directory", file=sys.stderr)
        sys.exit(1)

    # Spec §6 / AC #2: re-running init() must materialize new host entries.
    # An existing marker is the expected re-run path, not a failure — log it
    # and fall through to the extension loop, which is itself idempotent.
    # ``--force`` still rewrites the marker, consistent with the trust step.
    try:
        path = create_marker(target, environment, hosts=DEFAULT_HOSTS, force=force)
        print(f"Created {path}")
    except FileExistsError:
        marker_path = target / ".otsinfra.yaml"
        print(f"Marker already exists at {marker_path}")

    # Spec §107: .otsinfra.yaml is the source of truth for the host set.
    # Re-read the marker we just wrote so trust generation uses the canonical
    # roles rather than re-deriving from DEFAULT_HOSTS independently.
    marker_data = load_marker(target / ".otsinfra.yaml")
    marker_hosts = marker_data.get("hosts") if isinstance(marker_data, dict) else None
    if isinstance(marker_hosts, dict) and marker_hosts:
        host_roles = list(marker_hosts.keys())
    else:
        host_roles = list(DEFAULT_HOSTS.keys())

    def _trust_step(target: Path, *, force: bool = False) -> Path:
        return create_trust_material(
            target,
            hosts=host_roles,
            force=force,
            ca_days=ca_days,
            leaf_days=leaf_days,
        )

    for create_fn in (create_gitignore, create_envrc_template, _trust_step):
        try:
            path = create_fn(target, force=force)
            print(f"Created {path}")
        except FileExistsError as e:
            print(f"Warning: {e}", file=sys.stderr)

    print("Run 'direnv allow' after editing .envrc")
