"""v051 (BIZ-291): saved slices keep matching after the provider became part of every cache key."""
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.migrations import v051_slice_cache_provider_keys as mig
from app.migrations.runner import rollback_last, run_migrations
from app.models import SlicedVersion, UploadedFile
from app.services import slice_cache

pytestmark = pytest.mark.asyncio

CASES = [
    dict(plate_number=1, machine_preset="M", process_preset="P", filament_presets=["F"], extra_config={}, tool_index=None,
         filament_map=None, artifact_kind="gcode"),
    dict(plate_number=2, machine_preset="Snapmaker U1", process_preset="0.2 Standard", filament_presets=["PLA", "PETG"],
         extra_config={"curr_bed_type": "Textured PEI Plate", "sparse_infill_density": "15%"}, tool_index=2,
         filament_map=[[1, 0], [2, 3]], artifact_kind="gcode_3mf"),
]


def _inputs(c, provider=None):
    kw = dict(source_content_hash="srchash", plate_number=c["plate_number"], machine_preset=c["machine_preset"],
              process_preset=c["process_preset"], filament_presets=tuple(c["filament_presets"]), extra_config=c["extra_config"],
              tool_index=c["tool_index"], filament_map=c["filament_map"], artifact_kind=c["artifact_kind"])
    return slice_cache.CacheKeyInputs(**kw, **({"provider": provider} if provider else {}))


def _legacy_key(c):
    d = _inputs(c).as_dict()
    del d["provider"]                                  # the shape before provider was hashed for the default
    return slice_cache.sha256_of(d)


async def test_v051_rewrites_keys_to_the_current_shape_and_down_restores_the_old_one(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'm.db'}")
    try:
        async with engine.begin() as conn:
            await run_migrations(conn)
        async with async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)() as s:
            for i, c in enumerate(CASES):
                f = UploadedFile(original_filename=f"v{i}.gcode", relative_path=f"Prints/v{i}.gcode", folder="/Prints",
                                 content_hash=f"h{i}", plates=[], uploaded_at="2026-01-01T00:00:00+00:00")
                s.add(f)
                await s.flush()
                s.add(SlicedVersion(file_id=f.id, source_content_hash="srchash", cache_key=_legacy_key(c),
                                    created_at="2026-01-01T00:00:00+00:00", **c))
            await s.commit()
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM schema_migrations WHERE version = 51"))    # as if upgrading from v050
            await run_migrations(conn)
            keys = [r[0] for r in (await conn.execute(text("SELECT cache_key FROM sliced_versions ORDER BY id"))).fetchall()]
            # The migrated keys are byte-for-byte what the production key builder produces now.
            assert keys == [slice_cache.cache_key(_inputs(c)) for c in CASES]
            assert keys != [_legacy_key(c) for c in CASES]
            await rollback_last(conn)                                  # v052 (newer than the migration under test)
            await rollback_last(conn)                                  # v051
            keys = [r[0] for r in (await conn.execute(text("SELECT cache_key FROM sliced_versions ORDER BY id"))).fetchall()]
            assert keys == [_legacy_key(c) for c in CASES]
    finally:
        await engine.dispose()


async def test_v051_is_idempotent_and_a_noop_on_an_empty_table(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'e.db'}")
    try:
        async with engine.begin() as conn:
            await run_migrations(conn)
            await mig.up(conn)
            await mig.up(conn)
            assert (await conn.execute(text("SELECT count(*) FROM sliced_versions"))).scalar() == 0
    finally:
        await engine.dispose()
