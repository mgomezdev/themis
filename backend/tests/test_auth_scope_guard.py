"""require_scope's typo guard must survive python -O (issue #38)."""

import subprocess
import sys
from pathlib import Path

import pytest

from app.auth import require_scope


def test_known_scope_accepted():
    assert require_scope("jobs:read") is not None


def test_unknown_scope_raises_value_error():
    with pytest.raises(ValueError):
        require_scope("jobs:reed")


def test_guard_survives_python_O():
    """assert statements are stripped under -O; the guard must not be one."""
    code = "from app.auth import require_scope; require_scope('bogus:scope')"
    proc = subprocess.run(
        [sys.executable, "-O", "-c", code],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    assert "ValueError" in proc.stderr
