"""v049 (BIZ-286) through the real migration runner and the API: pre-migration rows keep serving their thumbnails."""
import json
from unittest.mock import patch

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.migrations import v049_thumbnail_keys as mig
from app.migrations.runner import rollback_last, run_migrations
from app.models import UploadedFile

OLD_PLATES = [
    {"plate_number": 1, "thumbnail_path": "/data/filecache/{id}/thumbnails/plate_1.png", "estimated_time": 60, "filament_g": 5.0},
    {"plate_number": 2, "thumbnail_path": "C:\\Themis\\filecache\\{id}\\thumbnails\\plate_2.png", "estimated_time": 0, "filament_g": 0.0},
]


def _row():
    return UploadedFile(original_filename="m.3mf", relative_path="m.3mf", folder="/", size_bytes=1, content_hash="h",
                        mtime=1.0, plates=[], missing=False, uploaded_at="2026-01-01T00:00:00")


def _old(file_id):
    return json.loads(json.dumps(OLD_PLATES).replace("{id}", str(file_id)))


async def _stored(conn, file_id):
    return json.loads((await conn.execute(text("SELECT plates FROM uploaded_files WHERE id=:i"), {"i": file_id})).scalar_one())


async def test_runner_applies_v049_to_an_existing_install_and_rollback_keeps_the_keys(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'm.db'}")
    try:
        async with engine.begin() as conn:
            await run_migrations(conn)
        async with async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)() as s:
            row = _row()
            s.add(row)
            await s.flush()
            row.plates = _old(row.id)
            await s.commit()
            file_id = row.id
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM schema_migrations WHERE version = 49"))   # as if upgrading from v048
            await run_migrations(conn)
            applied = "SELECT count(*) FROM schema_migrations WHERE version = 49"
            assert (await conn.execute(text(applied))).scalar() == 1
            assert [p["thumbnail_path"] for p in await _stored(conn, file_id)] == [
                f"{file_id}/thumbnails/plate_1.png", f"{file_id}/thumbnails/plate_2.png"]
            await rollback_last(conn)                                  # v056 (newer than the migration under test)
            await rollback_last(conn)                                  # v055
            await rollback_last(conn)                                  # v054
            await rollback_last(conn)                                  # v053 (newer than the migration under test)
            await rollback_last(conn)                                  # v052
            await rollback_last(conn)                                  # v051
            await rollback_last(conn)                                  # v050
            await rollback_last(conn)                                  # v049
            assert (await conn.execute(text(applied))).scalar() == 0
            assert (await _stored(conn, file_id))[0]["thumbnail_path"] == f"{file_id}/thumbnails/plate_1.png"
    finally:
        await engine.dispose()


async def test_thumbnails_of_pre_migration_rows_are_served_and_linked(client, session_factory, tmp_path):
    async with session_factory() as s:
        row = _row()
        s.add(row)
        await s.flush()
        row.plates = _old(row.id)
        await s.commit()
        file_id = row.id
    thumbs = tmp_path / "filecache" / str(file_id) / "thumbnails"
    thumbs.mkdir(parents=True)
    (thumbs / "plate_1.png").write_bytes(b"\x89PNG1")
    (thumbs / "plate_2.png").write_bytes(b"\x89PNG2")
    async with session_factory.kw["bind"].begin() as conn:
        await mig.up(conn)
    with patch("app.config.get_filecache_dir", return_value=tmp_path / "filecache"):
        listed = (await client.get("/api/v1/files")).json()[0]["plate_thumbnails"]
        assert [t["thumbnail_url"] for t in listed] == [f"/api/v1/files/{file_id}/thumbnails/plate_{n}.png" for n in (1, 2)]
        for n in (1, 2):
            resp = await client.get(f"/api/v1/files/{file_id}/thumbnails/plate_{n}.png")
            assert resp.status_code == 200 and resp.content == b"\x89PNG" + str(n).encode()
        plates = (await client.get(f"/api/v1/files/{file_id}/plates")).json()["plates"]
        assert [p["thumbnail_path"] for p in plates] == [f"{file_id}/thumbnails/plate_{n}.png" for n in (1, 2)]
