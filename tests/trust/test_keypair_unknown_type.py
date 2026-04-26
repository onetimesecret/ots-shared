# tests/trust/test_keypair_unknown_type.py

"""Negative-path test for unknown key_type values.

Spec ref: §3 (one primitive across ssh/wg/tls, key_type is the discriminant).
The existing matrix asserts that ``key_type='tls'`` requires ``ca=`` but
does not cover an unknown value — important because the type system uses
``Literal["ssh", "wg", "tls"]`` and a runtime caller (CLI args, config
file) can plausibly pass arbitrary strings.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ots_shared.trust import generate_keypair


def test_unknown_key_type_raises_value_error(trust_dir: Path) -> None:
    """§3: an unrecognised key_type must raise ValueError, not silently no-op."""
    out = trust_dir / "hosts" / "unknown-kt"
    with pytest.raises(ValueError) as excinfo:
        generate_keypair("rsa", "host-a", out)  # type: ignore[arg-type]

    # Message should mention the offending type so CLI users have a hint.
    assert "rsa" in str(excinfo.value).lower() or "key_type" in str(excinfo.value).lower(), (
        f"ValueError must reference the bad key_type; got: {excinfo.value!r}"
    )
