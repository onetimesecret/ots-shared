# src/ots_shared/init.py

"""Shared ``init`` sub-app for OTS CLI tools.

Creates the ``.otsinfra.yaml`` marker file that signals "this is an
OTS environment directory" to all OTS tools (lots, pots, rots).

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
)

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
            help="Overwrite existing init files (.otsinfra.yaml, .gitignore, .envrc template)"
        ),
    ] = False,
) -> None:
    """Create .otsinfra.yaml environment marker.

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

    try:
        path = create_marker(target, environment, hosts=DEFAULT_HOSTS, force=force)
        print(f"Created {path}")
    except FileExistsError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    for create_fn in (create_gitignore, create_envrc_template):
        try:
            path = create_fn(target, force=force)
            print(f"Created {path}")
        except FileExistsError as e:
            print(f"Warning: {e}", file=sys.stderr)

    print("Run 'direnv allow' after editing .envrc")
