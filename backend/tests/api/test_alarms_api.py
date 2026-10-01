"""Alarm feed API, fleet badge, settings, and the PrinterManager → alarms path."""
from unittest.mock import MagicMock

import pytest

from app.services import alarms as alarm_service
from app.services.abstract_printer_client import Alarm, PrinterCapabilities
from app.services.printer_manager import printer_manager


def A(code, sev="error", msg="m"):
    return Alarm(code=code, severity=sev, message=msg, source="hms", help_url=None)


async def raise_alarms(session_factory, printer_id, *alarms):
    async with session_factory() as s:
        return await alarm_service.reconcile(s, printer_id, list(alarms))


async def test_list_filters_by_status_printer_and_severity_newest_first(client, session_factory, create_printer):
    p1, p2 = await create_printer(name="Atlas"), await create_printer(name="Borealis")
    await raise_alarms(session_factory, p1, A("OLD", "info"), A("KEEP", "warning"))
    await raise_alarms(session_factory, p1, A("KEEP", "warning"))                    # OLD resolved
    await raise_alarms(session_factory, p2, A("BAD", "fatal", "Heater fault"))

    active = (await client.get("/api/v1/alarms")).json()
    assert {a["code"] for a in active} == {"KEEP", "BAD"} and all(a["active"] for a in active)
    bad = next(a for a in active if a["code"] == "BAD")
    assert (bad["printer_name"], bad["severity"], bad["message"]) == ("Borealis", "fatal", "Heater fault")
    assert {a["code"] for a in (await client.get("/api/v1/alarms", params={"status": "all"})).json()} == {"OLD", "KEEP", "BAD"}
    assert [a["code"] for a in (await client.get("/api/v1/alarms", params={"printer_id": p2})).json()] == ["BAD"]
    assert [a["code"] for a in (await client.get("/api/v1/alarms", params={"min_severity": "fatal"})).json()] == ["BAD"]
    history = (await client.get("/api/v1/alarms", params={"status": "all"})).json()
    old = next(a for a in history if a["code"] == "OLD")
    assert old["active"] is False and old["resolved_at"]
    assert (await client.get("/api/v1/alarms", params={"status": "weird"})).status_code == 422


async def test_acknowledge_silences_but_does_not_resolve_and_is_idempotent(client, session_factory, create_printer):
    pid = await create_printer()
    await raise_alarms(session_factory, pid, A("X"))
    (a,) = (await client.get("/api/v1/alarms")).json()

    r1 = (await client.post(f"/api/v1/alarms/{a['id']}/acknowledge")).json()
    r2 = (await client.post(f"/api/v1/alarms/{a['id']}/acknowledge")).json()

    assert r1["acknowledged_at"] and r1["acknowledged_at"] == r2["acknowledged_at"] and r1["active"] is True
    assert (await client.get("/api/v1/alarms", params={"status": "unacknowledged"})).json() == []
    assert len((await client.get("/api/v1/alarms")).json()) == 1                      # still listed as active
    assert (await client.post("/api/v1/alarms/999/acknowledge")).status_code == 404


async def test_acknowledge_all_can_be_scoped_to_one_printer(client, session_factory, create_printer):
    p1, p2 = await create_printer(name="a"), await create_printer(name="b")
    await raise_alarms(session_factory, p1, A("1"), A("2"))
    await raise_alarms(session_factory, p2, A("3"))
    assert (await client.post("/api/v1/alarms/acknowledge-all", params={"printer_id": p1})).json() == {"acknowledged": 2}
    assert [a["code"] for a in (await client.get("/api/v1/alarms", params={"status": "unacknowledged"})).json()] == ["3"]
    assert (await client.post("/api/v1/alarms/acknowledge-all")).json() == {"acknowledged": 1}


async def test_summary_and_fleet_badge_count_only_active_unacknowledged_alarms_with_the_worst_severity(client, session_factory, create_printer):
    p1, p2, p3 = await create_printer(name="a"), await create_printer(name="b"), await create_printer(name="c")
    await raise_alarms(session_factory, p1, A("W", "warning"), A("E", "error"), A("I", "info"))
    await raise_alarms(session_factory, p2, A("F", "fatal"))
    await raise_alarms(session_factory, p3, A("GONE", "fatal"))
    await raise_alarms(session_factory, p3)                                            # resolved → not counted
    (info,) = [a for a in (await client.get("/api/v1/alarms")).json() if a["code"] == "I"]
    await client.post(f"/api/v1/alarms/{info['id']}/acknowledge")                       # acknowledged → not counted

    s = (await client.get("/api/v1/alarms/summary")).json()
    assert (s["count"], s["worst"]) == (3, "fatal")
    assert s["printers"] == [{"printer_id": p1, "count": 2, "worst": "error"}, {"printer_id": p2, "count": 1, "worst": "fatal"}]

    fleet = {p["id"]: p for p in (await client.get("/api/v1/fleet")).json()}
    assert (fleet[p1]["alarm_count"], fleet[p1]["alarm_severity"]) == (2, "error")
    assert (fleet[p2]["alarm_count"], fleet[p2]["alarm_severity"]) == (1, "fatal")
    assert (fleet[p3]["alarm_count"], fleet[p3]["alarm_severity"]) == (0, None)


async def test_empty_summary(client):
    assert (await client.get("/api/v1/alarms/summary")).json() == {"count": 0, "worst": None, "printers": []}


async def test_settings_round_trip_and_validation(client):
    assert (await client.get("/api/v1/alarms/settings")).json() == {
        "min_severity": "warning", "severities": ["info", "warning", "error", "fatal"]}
    assert (await client.put("/api/v1/alarms/settings", json={"min_severity": "error"})).json()["min_severity"] == "error"
    assert (await client.get("/api/v1/alarms/settings")).json()["min_severity"] == "error"
    assert (await client.put("/api/v1/alarms/settings", json={"min_severity": "loud"})).status_code == 422
    assert (await client.get("/api/v1/alarms/settings")).json()["min_severity"] == "error"


async def test_the_printer_manager_turns_a_clients_reported_problems_into_alarms_and_resolves_them(client, session_factory, create_printer):
    pid = await create_printer(name="Atlas")
    mock = MagicMock()
    mock.connected = True
    mock.get_capabilities.return_value = PrinterCapabilities()
    reported: list[Alarm] = [A("HMS_0700", "error", "AMS: out")]
    mock.get_alarms.side_effect = lambda: list(reported)
    printer_manager._clients[pid] = mock
    printer_manager.set_session_factory(session_factory)
    broadcasts = []

    async def broadcast(kind, data):
        broadcasts.append(kind)
    printer_manager.set_broadcast_callback(broadcast)
    alarm_service.tracker.forget(pid)
    try:
        await printer_manager._observe_alarms(pid)
        await printer_manager._observe_alarms(pid)                                       # unchanged → no second row
        assert [a["code"] for a in (await client.get("/api/v1/alarms")).json()] == ["HMS_0700"]
        assert broadcasts.count("alarms_changed") == 1

        reported.clear()
        await printer_manager._observe_alarms(pid)
        assert (await client.get("/api/v1/alarms")).json() == []
        assert len((await client.get("/api/v1/alarms", params={"status": "all"})).json()) == 1
    finally:
        printer_manager.set_broadcast_callback(None)
        printer_manager._session_factory = None
        alarm_service.tracker.forget(pid)


async def test_a_client_that_raises_does_not_break_state_handling(client, create_printer, session_factory):
    pid = await create_printer()
    mock = MagicMock()
    mock.get_alarms.side_effect = RuntimeError("firmware weirdness")
    printer_manager._clients[pid] = mock
    printer_manager.set_session_factory(session_factory)
    try:
        await printer_manager._observe_alarms(pid)                                       # logged, not raised
    finally:
        printer_manager._session_factory = None
