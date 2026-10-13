"""Migration v049 (BIZ-286): absolute plates[].thumbnail_path -> filecache key `<file_id>/thumbnails/<name>`."""
import json

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.migrations import v049_thumbnail_keys as mig


@pytest.fixture
async def conn():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as c:
        await c.execute(text("CREATE TABLE uploaded_files (id INTEGER PRIMARY KEY, plates JSON)"))
        yield c
    await engine.dispose()


async def _plates(c, i):
    raw = (await c.execute(text("SELECT plates FROM uploaded_files WHERE id=:i"), {"i": i})).scalar_one()
    return json.loads(raw)


async def _insert(c, i, plates):
    await c.execute(text("INSERT INTO uploaded_files (id, plates) VALUES (:i, :p)"),
                    {"i": i, "p": plates if plates is None else json.dumps(plates)})


async def test_absolute_posix_and_windows_paths_become_keys_and_other_fields_survive(conn):
    await _insert(conn, 7, [
        {"plate_number": 1, "thumbnail_path": "/data/filecache/7/thumbnails/plate_1.png", "estimated_time": 60, "filament_g": 5.0},
        {"plate_number": 2, "thumbnail_path": "C:\\Themis\\filecache\\7\\thumbnails\\plate_2.png", "estimated_time": 0, "filament_g": 0.0},
        {"plate_number": 3, "thumbnail_path": None, "estimated_time": 0, "filament_g": 0.0},
    ])
    await mig.up(conn)
    plates = await _plates(conn, 7)
    assert [p["thumbnail_path"] for p in plates] == ["7/thumbnails/plate_1.png", "7/thumbnails/plate_2.png", None]
    assert (plates[0]["plate_number"], plates[0]["estimated_time"], plates[0]["filament_g"]) == (1, 60, 5.0)


async def test_up_is_idempotent_and_leaves_keys_and_odd_rows_alone(conn):
    await _insert(conn, 1, [{"plate_number": 1, "thumbnail_path": "1/thumbnails/plate_1.png"}])
    await _insert(conn, 2, None)
    await _insert(conn, 3, [])
    await conn.execute(text("INSERT INTO uploaded_files (id, plates) VALUES (4, 'not json')"))
    await mig.up(conn)
    await mig.up(conn)
    assert await _plates(conn, 1) == [{"plate_number": 1, "thumbnail_path": "1/thumbnails/plate_1.png"}]
    assert await _plates(conn, 3) == []


async def test_down_is_a_noop(conn):
    await _insert(conn, 5, [{"plate_number": 1, "thumbnail_path": "/x/5/thumbnails/plate_1.png"}])
    await mig.up(conn)
    await mig.down(conn)
    assert (await _plates(conn, 5))[0]["thumbnail_path"] == "5/thumbnails/plate_1.png"
