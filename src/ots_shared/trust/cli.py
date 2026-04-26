# packages/ots-shared/src/ots_shared/trust/cli.py

"""Operator subcommands for inspecting and pruning .trust/ material.

These commands consume the on-disk artifacts produced by the shared init
implementation in ``ots_shared.trust``. They never generate material.

Subcommands:
    fingerprints   Print fingerprints from the manifest for runbook verification.
    list           Show on-disk hosts vs. the host set declared in .otsinfra.yaml.
    prune          Remove a host's material and manifest entries.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Annotated

import cyclopts

from ots_shared.ssh.env import find_marker, load_marker
from ots_shared.trust import (
    OtsInfraMarkerMissingError,
    resolve_trust_dir,
    trust_flock,
)
from ots_shared.trust.manifest import Manifest

app = cyclopts.App(
    name="trust",
    help=(
        "Inspect and manage trust material under .trust/. Subcommands: fingerprints, list, prune."
    ),
)


HOSTS_DIRNAME = "hosts"
MANIFEST_FILENAME = "manifest.yaml"


def _role_dir(trust_dir: Path, role: str) -> Path:
    """Resolve a role's on-disk material directory.

    SOCKS material lives at ``.trust/socks/``, not under ``hosts/``,
    because it's a singleton identity, not a per-host key.
    """
    if role == "socks":
        return trust_dir / "socks"
    return trust_dir / HOSTS_DIRNAME / role


def _require_trust_dir() -> Path:
    try:
        trust_dir = resolve_trust_dir()
    except OtsInfraMarkerMissingError:
        print(
            "Error: .otsinfra.yaml not found; run from inside an OTS environment.",
            file=sys.stderr,
        )
        sys.exit(1)
    if not trust_dir.is_dir():
        print(
            f"Error: {trust_dir} not found; run `lots init` to materialize trust material.",
            file=sys.stderr,
        )
        sys.exit(1)
    return trust_dir


def _load_manifest(trust_dir: Path) -> Manifest:
    return Manifest.load(trust_dir / MANIFEST_FILENAME)


def _declared_roles() -> set[str]:
    marker = find_marker()
    if marker is None:
        return set()
    data = load_marker(marker)
    if not isinstance(data, dict):
        return set()
    declared: set[str] = set()
    hosts = data.get("hosts")
    if isinstance(hosts, dict):
        declared.update(str(role) for role in hosts)
    # SOCKS is declared via a top-level ``socks:`` block (singleton, not
    # a host role). Presence of the key is treated as a declaration; the
    # block's contents are operator-defined.
    if "socks" in data:
        declared.add("socks")
    return declared


@app.command(name="fingerprints")
def fingerprints() -> None:
    """Print fingerprints from the manifest for runbook verification.

    One line per entry: ``<role>  <key_type>  <fingerprint>``. Sorted by
    (role, key_type) so output is stable for diffing. Reads from
    ``.trust/manifest.yaml`` rather than re-deriving from on-disk material —
    the manifest is what was committed at generation time, which is what
    runbooks compare against.
    """
    trust_dir = _require_trust_dir()
    manifest = _load_manifest(trust_dir)
    entries = sorted(manifest.all(), key=lambda e: (e.name, e.key_type))
    if not entries:
        print("(manifest contains no entries)")
        return
    role_w = max(len(e.name) for e in entries)
    type_w = max(len(e.key_type) for e in entries)
    for entry in entries:
        print(f"{entry.name:<{role_w}}  {entry.key_type:<{type_w}}  {entry.fingerprint}")


@app.command(name="list")
def list_() -> None:
    """Show on-disk hosts vs. host set declared in .otsinfra.yaml.

    Output sections (in this order):
        present in both
        declared but missing on disk
        on disk but undeclared

    Hosts within each section are sorted. Exits zero even when drift is
    present — this command is informational. An absent ``.trust/`` is a
    different state and exits nonzero with a clear message.
    """
    trust_dir = _require_trust_dir()
    declared = _declared_roles()

    hosts_dir = trust_dir / HOSTS_DIRNAME
    on_disk: set[str] = set()
    if hosts_dir.is_dir():
        on_disk = {p.name for p in hosts_dir.iterdir() if p.is_dir()}
    # SOCKS lives at .trust/socks/ as a singleton, not under hosts/.
    if _role_dir(trust_dir, "socks").is_dir():
        on_disk.add("socks")

    both = sorted(declared & on_disk)
    missing = sorted(declared - on_disk)
    undeclared = sorted(on_disk - declared)

    print("present in both:")
    if both:
        for role in both:
            print(f"  {role}")
    else:
        print("  (none)")

    print("declared but missing on disk:")
    if missing:
        for role in missing:
            print(f"  {role}")
    else:
        print("  (none)")

    print("on disk but undeclared:")
    if undeclared:
        for role in undeclared:
            print(f"  {role}")
    else:
        print("  (none)")


@app.command(name="prune")
def prune(
    role: Annotated[
        str,
        cyclopts.Parameter(help="Role name to prune (e.g. db, web, jumphost)."),
    ],
    *,
    declared_ok: Annotated[
        bool,
        cyclopts.Parameter(
            name="--declared-ok",
            help="Allow pruning even if the role is still declared in .otsinfra.yaml.",
        ),
    ] = False,
    yes: Annotated[
        bool,
        cyclopts.Parameter(
            name=["--yes", "-y"],
            help="Skip the interactive confirmation prompt.",
        ),
    ] = False,
) -> None:
    """Remove a host's material and manifest entries.

    Deletes ``.trust/hosts/<role>/`` and removes the corresponding manifest
    entries. The CA cannot be pruned through this command. By default, a
    role still declared in ``.otsinfra.yaml`` is refused — silently pruning
    a declared host courts a re-init that quietly regenerates material with
    a different fingerprint, breaking deployed peers. ``--declared-ok``
    overrides this when the operator has already removed the host from the
    marker but wants to delete material in a separate step.
    """
    # Refuse the CA: pruning the CA is a separate, staged operation that
    # invalidates every leaf and requires dual-trust rollout. Not a job for
    # a per-host prune.
    if role == "ca":
        print(
            "Error: refusing to prune the CA; CA rotation is a separate operation.",
            file=sys.stderr,
        )
        sys.exit(1)

    trust_dir = _require_trust_dir()
    role_dir = _role_dir(trust_dir, role)

    declared = _declared_roles()
    if role in declared and not declared_ok:
        print(
            f"Error: role '{role}' is still declared in .otsinfra.yaml; "
            "remove it from the marker first or pass --declared-ok.",
            file=sys.stderr,
        )
        sys.exit(1)

    manifest_path = trust_dir / MANIFEST_FILENAME

    # Pre-flock existence check is informational only (the flocked block
    # re-reads the manifest before mutating). It lets us bail before
    # prompting the operator when there is provably nothing to do.
    pre_check_manifest = Manifest.load(manifest_path)
    pre_check_matching = [e for e in pre_check_manifest.entries if e.name == role]
    if not role_dir.exists() and not pre_check_matching:
        print(f"Error: role '{role}' has no material under {trust_dir}.", file=sys.stderr)
        sys.exit(1)

    # Confirm before destruction. Non-TTY (CI/scripts) must pass --yes
    # explicitly: a missed flag in a script should fail loud, not silently
    # destroy material.
    if not yes:
        if not sys.stdin.isatty():
            print(
                f"Error: refusing to prune '{role}' non-interactively without --yes.",
                file=sys.stderr,
            )
            sys.exit(1)
        prompt = f"Prune trust material for role '{role}'? Type 'yes' to confirm: "
        try:
            answer = input(prompt).strip().lower()
        except EOFError:
            answer = ""
        if answer != "yes":
            print("Aborted.", file=sys.stderr)
            sys.exit(1)

    # Hold the same flock create_trust_material uses so a concurrent
    # init cannot race the manifest read-modify-write or the directory
    # removal. Lock target is the checkout root (parent of .trust/) per
    # the rationale in trust_flock's docstring.
    checkout_root = trust_dir.parent
    deleted_paths: list[str] = []
    matching: list = []
    with trust_flock(checkout_root):
        manifest = Manifest.load(manifest_path)
        matching = [e for e in manifest.entries if e.name == role]

        if role_dir.exists():
            shutil.rmtree(role_dir)
            deleted_paths.append(str(role_dir))

        if matching:
            manifest.entries = [e for e in manifest.entries if e.name != role]
            manifest.save(manifest_path)

    print(f"Pruned role '{role}':")
    for path in deleted_paths:
        print(f"  removed {path}")
    for entry in matching:
        print(f"  removed manifest entry {entry.name}/{entry.key_type}")
    if not deleted_paths and not matching:
        print("  (nothing to do)")
