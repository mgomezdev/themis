import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.database import Base
from app.models import Printer, UploadedFile, Job, JobPrinterConfig, NotificationConfig, Project


TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture
async def session():
    engine = create_async_engine(TEST_DB_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


async def test_new_printer_and_job_config_rows_start_with_safe_defaults(session):
    """Column defaults the queue engine and the UI rely on: a new printer accepts work
    (enabled, queue on, plate clear) and a new job config has not failed slicing."""
    printer = Printer(name="X1C", printer_type="bambu", connection_config={}, orca_printer_profiles=[])
    uploaded_file = UploadedFile(original_filename="model.3mf", stored_path="/x", plates=[],
                                 uploaded_at="2026-05-20T12:00:00Z")
    session.add_all([printer, uploaded_file])
    await session.commit()
    job = Job(uploaded_file_id=uploaded_file.id, plate_number=1, queue_position=1.0, status="queued",
              created_at="2026-05-20T12:00:00Z", updated_at="2026-05-20T12:00:00Z")
    session.add(job)
    await session.commit()
    config = JobPrinterConfig(job_id=job.id, printer_id=printer.id,
                              print_profile="0.20mm Standard @BBL X1C",
                              filament_profile="Bambu PLA Basic @BBL X1C")
    session.add(config)
    await session.commit()
    await session.refresh(printer)
    await session.refresh(config)

    assert (printer.enabled, printer.queue_on, printer.awaiting_plate_clear) == (True, True, False)
    assert (config.slice_failed, config.slice_error) == (False, None)


async def test_notification_config_json_list_columns_round_trip(session):
    cfg = NotificationConfig(id=1)
    session.add(cfg)
    await session.commit()
    await session.refresh(cfg)

    assert cfg.ntfy_enabled is False
    assert cfg.discord_enabled is False
    assert cfg.email_enabled is False
    assert cfg.ntfy_events == []
    assert cfg.discord_events == []
    assert cfg.email_events == []
    assert cfg.email_to_addrs == []

    cfg.ntfy_enabled = True
    cfg.ntfy_events = ["job.complete"]
    cfg.discord_events = ["job.failed", "job.blocked"]
    cfg.email_to_addrs = ["ops@example.com", "alerts@example.com"]
    await session.commit()
    await session.refresh(cfg)

    assert cfg.ntfy_enabled is True
    assert cfg.ntfy_events == ["job.complete"]
    assert cfg.discord_events == ["job.failed", "job.blocked"]
    assert cfg.email_to_addrs == ["ops@example.com", "alerts@example.com"]


async def test_project_share_token_must_be_unique(session):
    now = "2026-09-06T00:00:00Z"
    p1 = Project(name="Project A", created_at=now, updated_at=now, share_token="dup-token")
    p2 = Project(name="Project B", created_at=now, updated_at=now, share_token="dup-token")
    session.add_all([p1, p2])
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_project_share_token_defaults_to_none(session):
    now = "2026-09-06T00:00:00Z"
    p = Project(name="Project C", created_at=now, updated_at=now)
    session.add(p)
    await session.commit()
    await session.refresh(p)
    assert p.share_token is None
    assert p.share_token_created_at is None
