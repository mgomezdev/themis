"""Golden-file helper (BIZ-204): pin today's wire format so later plugin phases can prove they didn't change it.

`assert_golden(name, value)` canonicalises `value` (sorted keys, 2-space indent, trailing newline) and compares it
byte-for-byte with `tests/golden/<name>.json`. `UPDATE_GOLDEN=1 pytest ...` rewrites the files; review the diff.
Volatile values (timestamps) must be masked by the caller with `mask()` so the files stay deterministic."""
from __future__ import annotations

import json
import os
from pathlib import Path

GOLDEN_DIR = Path(__file__).parent / "golden"


def canonical(value) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def golden_path(name: str) -> Path:
    return GOLDEN_DIR / f"{name}.json"


def load_golden(name: str):
    return json.loads(golden_path(name).read_text(encoding="utf-8"))


def assert_golden(name: str, value) -> None:
    path, actual = golden_path(name), canonical(value)
    if os.environ.get("UPDATE_GOLDEN") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(actual, encoding="utf-8", newline="\n")
        return
    assert path.exists(), f"missing golden file {path} (run with UPDATE_GOLDEN=1 to create it)"
    expected = path.read_text(encoding="utf-8")
    assert actual == expected, f"{name} drifted from its golden file; if intended, rerun with UPDATE_GOLDEN=1"


def mask(value, keys: set[str], placeholder: str = "<masked>"):
    """Copy of `value` with every non-null entry under `keys` (at any depth) replaced by `placeholder`."""
    if isinstance(value, dict):
        return {k: (placeholder if k in keys and v is not None else mask(v, keys, placeholder)) for k, v in value.items()}
    if isinstance(value, list):
        return [mask(v, keys, placeholder) for v in value]
    return value
