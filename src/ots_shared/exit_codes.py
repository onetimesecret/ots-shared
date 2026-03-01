"""Standardised exit codes for all OTS CLI tools.

All commands across ots-containers, hcloud-manager, otsinfra, and
ots-cloudinit use this scheme so that CI pipelines and shell scripts
can distinguish between different failure modes.

Usage::

    from ots_shared.exit_codes import EXIT_SUCCESS, EXIT_FAILURE, EXIT_PARTIAL, EXIT_PRECOND

    raise SystemExit(EXIT_PARTIAL)
"""

EXIT_SUCCESS: int = 0
"""Command completed successfully; all operations applied."""

EXIT_FAILURE: int = 1
"""General command failure (unexpected error, bad arguments, etc.)."""

EXIT_PARTIAL: int = 2
"""Partial success: at least one operation succeeded and at least one failed.

Check the command output for details on which operations failed."""

EXIT_PRECOND: int = 3
"""Precondition not met: required configuration is absent.

Examples: missing env file, missing secrets, image not pulled.
No destructive action was attempted."""
