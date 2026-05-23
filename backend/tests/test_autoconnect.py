import pytest
from unittest.mock import MagicMock, patch
from app.services.printer_manager import PrinterManager


@pytest.mark.asyncio
async def test_connect_all_enabled_printers_registers_clients():
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
    from app.database import Base
    from app.models import Printer

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        p1 = Printer(name="P1", printer_type="bambu",
                     connection_config={"ip_address": "1.2.3.4", "serial_number": "SN1", "access_code": "abc"},
                     enabled=True)
        p2 = Printer(name="P2", printer_type="bambu",
                     connection_config={"ip_address": "1.2.3.5", "serial_number": "SN2", "access_code": "def"},
                     enabled=False)
        session.add_all([p1, p2])
        await session.commit()
        await session.refresh(p1)
        await session.refresh(p2)

    pm = PrinterManager()
    pm.set_loop(MagicMock())

    mock_client = MagicMock()
    with patch("app.services.printer_manager.create_client", return_value=mock_client):
        with patch.object(pm, "connect_printer") as mock_connect:
            await pm.connect_all_enabled_printers(factory)
            assert mock_connect.call_count == 1
            assert mock_connect.call_args[0][0] == p1.id

    await engine.dispose()


@pytest.mark.asyncio
async def test_connect_all_skips_erroring_printer():
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
    from app.database import Base
    from app.models import Printer

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        p1 = Printer(name="P1", printer_type="bambu",
                     connection_config={"ip_address": "1.2.3.4", "serial_number": "SN1", "access_code": "abc"},
                     enabled=True)
        p2 = Printer(name="P2", printer_type="bambu",
                     connection_config={"ip_address": "1.2.3.5", "serial_number": "SN2", "access_code": "def"},
                     enabled=True)
        session.add_all([p1, p2])
        await session.commit()
        await session.refresh(p1)
        await session.refresh(p2)

    pm = PrinterManager()
    pm.set_loop(MagicMock())

    connect_calls = []
    def fake_connect(printer_id, client):
        connect_calls.append(printer_id)
        if len(connect_calls) == 1:
            raise RuntimeError("connection refused")

    with patch("app.services.printer_manager.create_client", return_value=MagicMock()):
        with patch.object(pm, "connect_printer", side_effect=fake_connect):
            await pm.connect_all_enabled_printers(factory)
            assert len(connect_calls) == 2

    await engine.dispose()
