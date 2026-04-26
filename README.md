# ots-shared

Shared constants, taxonomy, and utility libraries for the OneTimeSecret operations
tooling family (rots, lots, pots).

This package is not intended for end-user consumption. It is published to PyPI so the
downstream operator-facing CLIs can pin a single, versioned source of truth for:

- exit codes and error taxonomy
- command history records
- environment marker / scaffold helpers
- SSH connection and executor protocols
- Hetzner Cloud (`hcloud`) configuration, network, and server defaults

## Install

```bash
pip install ots-shared

# with optional SSH dependencies (paramiko)
pip install "ots-shared[ssh]"
```

## Layout

```
src/ots_shared/
  cli.py            # init sub-app exposed by downstream CLIs
  exit_codes.py     # canonical exit codes
  history.py        # command history serialization
  init.py           # environment scaffold helpers
  taxonomy.py       # error taxonomy
  hcloud/           # Hetzner Cloud config, networks, server defaults
  ssh/              # connection + executor protocols
```

## Development

```bash
uv sync --extra dev --extra test --extra ssh
uv run pytest tests/
uv run ruff check src/
uv run pyright src/
```

## Releasing

Tag a `vX.Y.Z` commit on `main`. The `Release` workflow builds the wheel and sdist,
creates a GitHub release with auto-generated notes, and publishes to PyPI via Trusted
Publisher. The PyPI environment in the workflow must match the project on
`pypi.org/p/ots-shared`.

## License

MIT — see [LICENSE](./LICENSE).
