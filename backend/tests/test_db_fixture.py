"""The shared `session_factory` must behave like the production database, or tests built on it prove nothing
about concurrency or referential integrity. (The old `sqlite+aiosqlite:///:memory:` fixture shared ONE
connection between sessions and had foreign keys off: a polling reader saw, and rolled back, another
session's uncommitted writes, and orphan rows were accepted.)"""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.models import Job, Printer


def _printer(name: str = "P") -> Printer:
    return Printer(name=name, printer_type="elegoo_centauri", connection_config={})


async def test_a_session_does_not_see_another_sessions_uncommitted_writes(session_factory):
    async with session_factory() as writer, session_factory() as reader:
        writer.add(_printer("draft"))
        await writer.flush()                                              # written, not committed

        seen_before = (await reader.execute(text("select count(*) from printers"))).scalar_one()
        await writer.commit()
        await reader.rollback()                                           # end the reader's snapshot
        seen_after = (await reader.execute(text("select count(*) from printers"))).scalar_one()

    assert (seen_before, seen_after) == (0, 1)


async def test_closing_one_session_does_not_roll_back_anothers_pending_work(session_factory):
    writer = session_factory()
    async with writer:
        writer.add(_printer("keeper"))
        await writer.flush()
        async with session_factory() as bystander:                        # e.g. a polling loop opening and closing
            await bystander.execute(text("select 1"))
        await writer.commit()

    async with session_factory() as check:
        assert (await check.execute(text("select name from printers"))).scalars().all() == ["keeper"]


async def test_foreign_keys_are_enforced_as_in_production(session_factory):
    async with session_factory() as s:
        s.add(Job(uploaded_file_id=99999, plate_number=1, status="queued", queue_position=1.0,
                  created_at="2026-01-01T00:00:00", updated_at="2026-01-01T00:00:00"))
        with pytest.raises(IntegrityError):
            await s.commit()


async def test_every_test_starts_from_an_empty_database(session_factory):
    async with session_factory() as s:
        assert (await s.execute(text("select count(*) from printers"))).scalar_one() == 0
        s.add(_printer("left behind"))
        await s.commit()
