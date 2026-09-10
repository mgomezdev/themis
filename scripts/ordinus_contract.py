#!/usr/bin/env python3
"""Print a stable digest of the Themis API surface that Ordinus consumes.

Ordinus talks to Themis through exactly four operations, all of them behind
`ordinus/server/src/services/themis.service.ts`. CI compares this digest between
a pull request's base and head to decide whether that pull request could break
Ordinus. When the digest is unchanged the Ordinus container is never started,
which keeps a typical Themis pull request at three containers instead of four.

The digest follows `$ref` closures, so a change to a schema these operations
depend on is caught even though the path entry itself is untouched.

Usage:
    python scripts/ordinus_contract.py [path/to/openapi.json]
"""
from __future__ import annotations

import hashlib
import json
import sys
from typing import Any

# (path, method) pairs Ordinus calls. Keep in sync with themis.service.ts.
CONSUMED: tuple[tuple[str, str], ...] = (
    ("/api/v1/health", "get"),
    ("/api/v1/files/upload", "post"),
    ("/api/v1/projects", "post"),
    ("/api/v1/projects/{project_id}/items", "post"),
)

_SCHEMA_PREFIX = "#/components/schemas/"


def _iter_refs(node: Any):
    """Yield every $ref string anywhere beneath node."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str):
                yield value
            else:
                yield from _iter_refs(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_refs(item)


def extract(spec: dict[str, Any]) -> dict[str, Any]:
    """Return the consumed operations plus the transitive schema closure."""
    paths = spec.get("paths", {})
    schemas = spec.get("components", {}).get("schemas", {})

    operations: dict[str, Any] = {}
    for path, method in CONSUMED:
        operation = paths.get(path, {}).get(method)
        # A missing operation is itself a contract change worth reporting, so
        # record the absence rather than skipping the entry.
        operations[f"{method.upper()} {path}"] = operation

    # Walk $refs to a fixed point so schema edits are caught too.
    closure: dict[str, Any] = {}
    pending = [r for r in _iter_refs(operations) if r.startswith(_SCHEMA_PREFIX)]
    while pending:
        ref = pending.pop()
        name = ref[len(_SCHEMA_PREFIX):]
        if name in closure:
            continue
        schema = schemas.get(name)
        closure[name] = schema
        pending.extend(r for r in _iter_refs(schema) if r.startswith(_SCHEMA_PREFIX))

    return {"operations": operations, "schemas": closure}


def digest(spec: dict[str, Any]) -> str:
    canonical = json.dumps(extract(spec), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def main() -> int:
    source = sys.argv[1] if len(sys.argv) > 1 else "openapi.json"
    try:
        with open(source, encoding="utf-8") as fh:
            spec = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        # An unreadable base spec (first run, or the file did not exist yet)
        # must not silently look identical to the head spec.
        print(f"unreadable:{exc.__class__.__name__}", file=sys.stderr)
        print("unreadable")
        return 0
    print(digest(spec))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
