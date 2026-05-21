import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.database import Base
from app.models import Printer, UploadedFile, Project, Job, JobPrinterConfig, GcodeFile


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


async def test_create_printer(session):
    printer = Printer(
        name="X1 Carbon",
        printer_type="bambu",
        connection_config={"serial_number": "ABC123", "access_code": "secret"},
        orca_printer_profiles=["Bambu Lab X1 Carbon 0.4"],
        current_orca_printer_profile="Bambu Lab X1 Carbon 0.4",
    )
    session.add(printer)
    await session.commit()
    await session.refresh(printer)
    assert printer.id is not None
    assert printer.awaiting_plate_clear is False
    assert printer.enabled is True


async def test_create_uploaded_file(session):
    f = UploadedFile(
        original_filename="model.3mf",
        stored_path="/data/uploads/abc/model.3mf",
        plates=[{"plate_number": 1, "thumbnail_path": "/data/uploads/abc/plate_1.png", "estimated_time": 3600, "filament_g": 42.1}],
        uploaded_at="2026-05-20T12:00:00Z",
    )
    session.add(f)
    await session.commit()
    await session.refresh(f)
    assert f.id is not None
    assert len(f.plates) == 1


async def test_create_job_with_printer_config(session):
    printer = Printer(
        name="X1C",
        printer_type="bambu",
        connection_config={},
        orca_printer_profiles=[],
    )
    uploaded_file = UploadedFile(
        original_filename="model.3mf",
        stored_path="/data/uploads/abc/model.3mf",
        plates=[],
        uploaded_at="2026-05-20T12:00:00Z",
    )
    session.add_all([printer, uploaded_file])
    await session.commit()

    job = Job(
        uploaded_file_id=uploaded_file.id,
        plate_number=1,
        queue_position=1.0,
        status="queued",
        created_at="2026-05-20T12:00:00Z",
        updated_at="2026-05-20T12:00:00Z",
    )
    session.add(job)
    await session.commit()

    config = JobPrinterConfig(
        job_id=job.id,
        printer_id=printer.id,
        print_profile="0.20mm Standard @BBL X1C",
        filament_profile="Bambu PLA Basic @BBL X1C",
    )
    session.add(config)
    await session.commit()
    await session.refresh(config)

    assert config.id is not None
    assert config.slice_failed is False
    assert config.slice_error is None


async def test_create_gcode_file(session):
    printer = Printer(name="P", printer_type="bambu", connection_config={}, orca_printer_profiles=[])
    uploaded_file = UploadedFile(original_filename="m.3mf", stored_path="/x", plates=[], uploaded_at="2026-05-20T00:00:00Z")
    session.add_all([printer, uploaded_file])
    await session.commit()

    job = Job(uploaded_file_id=uploaded_file.id, plate_number=1, queue_position=1.0, status="slicing", created_at="2026-05-20T00:00:00Z", updated_at="2026-05-20T00:00:00Z")
    session.add(job)
    await session.commit()

    gcode = GcodeFile(job_id=job.id, printer_id=printer.id, path="/data/gcode/1/output.gcode")
    session.add(gcode)
    await session.commit()
    await session.refresh(gcode)
    assert gcode.id is not None
