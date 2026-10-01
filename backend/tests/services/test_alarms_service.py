"""Alarm reconcile rules, delivery filtering, and the tracker, against the real DB."""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.models import NotificationConfig, PrinterAlarm, QueueConfig, WebhookConfig
from app.services import alarms
from app.services.abstract_printer_client import Alarm


def A(code="HMS_X", sev="error", msg="boom", **kw):
    return Alarm(code=code, severity=sev, message=msg, source="hms", **kw)


async def rows(factory, printer_id=None):
    async with factory() as s:
        q = select(PrinterAlarm).order_by(PrinterAlarm.id)
        if printer_id:
            q = q.where(PrinterAlarm.printer_id == printer_id)
        return (await s.execute(q)).scalars().all()


async def test_a_reported_code_becomes_one_active_row_and_repeats_do_not_duplicate(session_factory, create_printer):
    pid = await create_printer()
    async with session_factory() as s:
        first = await alarms.reconcile(s, pid, [A()])
    assert len(first) == 1
    async with session_factory() as s:
        again = await alarms.reconcile(s, pid, [A(msg="boom v2", sev="fatal")])
    assert again == []                                                          # same code: not new
    (row,) = await rows(session_factory)
    assert (row.message, row.severity, row.resolved_at) == ("boom v2", "fatal", None)   # refreshed in place
    assert row.last_seen >= row.first_seen


async def test_a_code_that_stops_being_reported_is_resolved_and_kept_as_history_and_a_return_is_a_new_occurrence(session_factory, create_printer):
    pid = await create_printer()
    async with session_factory() as s:
        await alarms.reconcile(s, pid, [A("A"), A("B")])
    async with session_factory() as s:
        await alarms.reconcile(s, pid, [A("B")])
    got = {r.code: r for r in await rows(session_factory)}
    assert got["A"].resolved_at is not None and got["B"].resolved_at is None

    async with session_factory() as s:
        fresh = await alarms.reconcile(s, pid, [A("A"), A("B")])
    assert [r.code for r in fresh] == ["A"]                                      # A is back → a new row/event
    assert [r.code for r in await rows(session_factory)] == ["A", "B", "A"]


async def test_alarms_are_per_printer(session_factory, create_printer):
    p1, p2 = await create_printer(name="one"), await create_printer(name="two")
    async with session_factory() as s:
        await alarms.reconcile(s, p1, [A("SAME")])
    async with session_factory() as s:
        fresh = await alarms.reconcile(s, p2, [A("SAME")])
        await alarms.reconcile(s, p1, [])
    assert len(fresh) == 1
    got = {r.printer_id: r.resolved_at for r in await rows(session_factory)}
    assert got[p1] is not None and got[p2] is None                               # resolving p1 left p2 alone


async def test_deleting_a_printer_removes_its_alarms(client, session_factory, create_printer):
    pid = await create_printer()
    async with session_factory() as s:
        await alarms.reconcile(s, pid, [A()])
    assert (await client.delete(f"/api/v1/printers/{pid}")).status_code in (200, 204)
    assert await rows(session_factory) == []


# ── delivery ─────────────────────────────────────────────────────────────────

async def _configure(factory, *, min_sev=None, events=None, webhook=True, ntfy=False):
    async with factory() as s:
        if min_sev:
            s.add(QueueConfig(id=1, alarm_min_severity=min_sev))
        if webhook:
            s.add(WebhookConfig(id=1, url="http://hook.test/x", secret=None, events=events or []))
        s.add(NotificationConfig(id=1, ntfy_enabled=ntfy, ntfy_server_url="http://ntfy.test", ntfy_topic="t",
                                 ntfy_events=[], discord_events=[], email_to_addrs=[], email_events=[]))
        await s.commit()


async def _deliver(factory, pid, alarm):
    from app.models import Printer
    async with factory() as s:
        (row,) = await alarms.reconcile(s, pid, [alarm])
        return await alarms.deliver(s, await s.get(Printer, pid), row)


@pytest.mark.parametrize("min_sev,sev,delivered", [
    ("warning", "info", False), ("warning", "warning", True), ("warning", "fatal", True),
    ("error", "warning", False), ("error", "error", True), ("fatal", "error", False), ("info", "info", True),
])
async def test_the_severity_filter_decides_whether_an_alarm_is_sent(session_factory, create_printer, min_sev, sev, delivered):
    pid = await create_printer(name="Atlas")
    await _configure(session_factory, min_sev=min_sev)
    with patch("app.services.alarms.webhook_service.schedule") as hook:
        assert await _deliver(session_factory, pid, A(sev=sev)) is delivered
    assert hook.called is delivered


async def test_the_default_filter_is_warning_when_nothing_is_configured(session_factory, create_printer):
    pid = await create_printer()
    await _configure(session_factory)
    with patch("app.services.alarms.webhook_service.schedule") as hook:
        assert await _deliver(session_factory, pid, A(code="I", sev="info")) is False
        assert await _deliver(session_factory, pid, A(code="W", sev="warning")) is True
    assert hook.call_count == 1


async def test_the_webhook_payload_and_the_event_list(session_factory, create_printer):
    pid = await create_printer(name="Atlas")
    await _configure(session_factory, events=["printer.alarm"])
    with patch("app.services.alarms.webhook_service.schedule") as hook:
        await _deliver(session_factory, pid, A(code="HMS_0700", sev="error", msg="AMS: out", help_url="http://w/1"))
    args = hook.call_args.args
    assert args[:2] == ("http://hook.test/x", None) and args[2] == "printer.alarm" and args[3] is None
    assert args[4] | {"alarm_id": 0} == {"printer_id": pid, "printer_name": "Atlas", "alarm_id": 0, "code": "HMS_0700",
                                          "severity": "error", "message": "AMS: out", "help_url": "http://w/1"}

    async with session_factory() as s:                                           # a webhook that doesn't subscribe gets nothing
        (await s.get(WebhookConfig, 1)).events = ["job.complete"]
        await s.commit()
    with patch("app.services.alarms.webhook_service.schedule") as hook:
        await _deliver(session_factory, pid, A(code="OTHER", sev="error"))
    hook.assert_not_called()


async def test_notification_channels_get_a_titled_message(session_factory, create_printer):
    pid = await create_printer(name="Atlas")
    await _configure(session_factory, webhook=False, ntfy=True)
    with patch("app.services.alarms.notification_service.dispatch", new_callable=AsyncMock) as dispatch:
        await _deliver(session_factory, pid, A(sev="fatal", msg="Heater fault"))
        await asyncio.sleep(0)
    dispatch.assert_called_once()
    assert dispatch.call_args.args[1:] == ("printer.alarm", None, "Themis: fatal on Atlas", "Heater fault")


async def test_a_delivery_failure_is_swallowed(session_factory, create_printer):
    pid = await create_printer()
    await _configure(session_factory)
    with patch("app.services.alarms.webhook_service.schedule", side_effect=RuntimeError("down")):
        assert await _deliver(session_factory, pid, A()) is False


# ── tracker ──────────────────────────────────────────────────────────────────

async def test_the_tracker_skips_unchanged_reports_retries_after_a_db_failure_and_broadcasts_changes(session_factory, create_printer):
    pid = await create_printer()
    t = alarms.AlarmTracker()
    sent = []

    async def broadcast(kind, data):
        sent.append((kind, data))

    await t.observe(session_factory, pid, [A()], broadcast)
    await t.observe(session_factory, pid, [A()], broadcast)                      # identical → no DB work, no broadcast
    assert len(await rows(session_factory)) == 1 and sent == [("alarms_changed", {"printer_id": pid})]

    with patch("app.services.alarms.reconcile", side_effect=RuntimeError("db down")):
        with pytest.raises(RuntimeError):
            await t.observe(session_factory, pid, [], broadcast)
    await t.observe(session_factory, pid, [], broadcast)                         # not remembered → retried and resolved
    assert (await rows(session_factory))[0].resolved_at is not None and len(sent) == 2

    t.forget(pid)
    assert pid not in t._last


async def test_a_report_for_a_deleted_printer_is_ignored(session_factory):
    await alarms.AlarmTracker().observe(session_factory, 999, [A()])
    assert await rows(session_factory) == []


async def test_purge_removes_only_old_resolved_alarms(session_factory, create_printer):
    pid = await create_printer()
    async with session_factory() as s:
        s.add_all([
            PrinterAlarm(printer_id=pid, code="old", severity="info", message="m", first_seen="2020-01-01T00:00:00+00:00",
                         last_seen="2020-01-01T00:00:00+00:00", resolved_at="2020-01-02T00:00:00+00:00"),
            PrinterAlarm(printer_id=pid, code="old-active", severity="info", message="m", first_seen="2020-01-01T00:00:00+00:00",
                         last_seen="2020-01-01T00:00:00+00:00"),
            PrinterAlarm(printer_id=pid, code="new", severity="info", message="m", first_seen=alarms.now(), last_seen=alarms.now(),
                         resolved_at=alarms.now()),
        ])
        await s.commit()
        assert await alarms.purge_old(s) == 1
    assert sorted(r.code for r in await rows(session_factory)) == ["new", "old-active"]
