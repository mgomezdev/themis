"""Slice-cache keys always carry the slicing provider (BIZ-291). Until now the default provider (`laminus`) was left OUT of the hashed
key inputs so keys saved before the provider field existed stayed valid; now the provider is always hashed. This recomputes every
`sliced_versions.cache_key` (the row stores all of its inputs) with `provider = 'laminus'` — every saved slice came from it — so saved
slices keep serving cache hits, cached-while-down prints (BIZ-201) and duplicate detection. `down` restores the old shape."""
from __future__ import annotations
import hashlib
import json

from sqlalchemy import text

version = 51
data_only = True        # changes rows, not schema (tests/test_migrations.py skips such a migration when it looks for a schema rollback)
name = "slice_cache_provider_keys"

PROVIDER = "laminus"


def _key(row, *, with_provider: bool) -> str:
    def load(v):
        return json.loads(v) if isinstance(v, str) else v
    inputs = {
        "source_content_hash": row.source_content_hash, "plate_number": row.plate_number,
        "machine_preset": row.machine_preset, "process_preset": row.process_preset,
        "filament_presets": list(load(row.filament_presets) or []), "extra_config": dict(load(row.extra_config) or {}),
        "tool_index": row.tool_index, "filament_map": list(load(row.filament_map)) if load(row.filament_map) else None,
        "artifact_kind": row.artifact_kind,
    }
    if with_provider:
        inputs["provider"] = PROVIDER
    # Identical to slice_cache.sha256_of / canonical_json: sorted keys, no whitespace, default=str.
    return hashlib.sha256(json.dumps(inputs, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


async def _rewrite(conn, *, with_provider: bool) -> None:
    rows = (await conn.execute(text(
        "SELECT id, source_content_hash, plate_number, machine_preset, process_preset, filament_presets, extra_config, "
        "tool_index, filament_map, artifact_kind FROM sliced_versions"))).fetchall()
    for row in rows:
        await conn.execute(text("UPDATE sliced_versions SET cache_key = :k WHERE id = :i"),
                           {"k": _key(row, with_provider=with_provider), "i": row.id})


async def up(conn) -> None:
    await _rewrite(conn, with_provider=True)


async def down(conn) -> None:
    await _rewrite(conn, with_provider=False)
