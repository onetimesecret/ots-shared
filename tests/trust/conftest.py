"""Fixtures for the trust material library tests.

All fixtures are scoped to ``tmp_path``. Nothing under the operator's real
``.trust/`` is touched.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ots_shared.trust.ca import CA, generate_ca


@pytest.fixture
def trust_dir(tmp_path: Path) -> Path:
    """Return the path the tests will treat as the operator ``.trust/`` root.

    Note: the directory is intentionally NOT created here. Tests verify that
    generation creates parent directories with the expected modes.
    """
    return tmp_path / ".trust"


@pytest.fixture
def ca(trust_dir: Path) -> CA:
    """A pre-generated CA under ``<trust_dir>/ca`` for tests that need one."""
    return generate_ca(trust_dir / "ca")
