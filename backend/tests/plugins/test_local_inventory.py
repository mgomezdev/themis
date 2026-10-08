"""The Local inventory plugin (BIZ-222): its tables and migration, the weight audit log, labels, and the plugin working as the
ACTIVE provider through the neutral API, preflight, alerts and the deduction model — with core knowing nothing about it."""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from app.plugins import get_plugin
from app.plugins.host import plugin_host
from app.plugins.capabilities.filament_inventory import (
    CAPABILITY, REMOTE, InventoryProviderError, MaterialDraft, SpoolDraft,
)
from app.plugins.local_inventory import MANIFEST
from app.plugins.local_inventory.migrations import v001_tables
from app.plugins.local_inventory.provider import LocalInventoryProvider
from app.plugins.local_inventory.settings import LocalInventorySettings
from app.plugins.migrations import run_plugin_migrations
from app.services.inventory import deduction, outbox, read, snapshots, tasks

BASE = "/api/v1/inventory"
PLUGIN = "/api/v1/plugins/local_inventory"


@pytest.fixture
def local(session_factory) -> LocalInventoryProvider:
    return LocalInventoryProvider(LocalInventorySettings())


async def _active(client):
    assert (await client.put("/api/v1/plugins/local_inventory", json={"enabled": True})).status_code == 200
    assert (await client.put(f"/api/v1/capabilities/{CAPABILITY}/provider", json={"plugin_id": "local_inventory"})).status_code == 200


async def _rows(factory, sql, **p):
    async with factory() as s:
        return [dict(r) for r in (await s.execute(text(sql), p)).mappings().all()]


# ---- manifest & migration ----------------------------------------------------------------------------------------------

def test_the_bundled_manifest_declares_a_non_remote_page_plugin_with_its_own_prefix():
    from app.plugins import load_bundled
    load_bundled()
    assert get_plugin("local_inventory") is MANIFEST
    assert REMOTE not in MANIFEST.provides[CAPABILITY].features and MANIFEST.table_prefix == "local_inv_"
    assert (MANIFEST.ui.mode, [(t.id, t.renderer) for t in MANIFEST.ui.tabs]) == ("page", [("settings", "default")])
    assert MANIFEST.provides[CAPABILITY].features == LocalInventoryProvider.capabilities


async def test_migration_runs_on_a_fresh_db_is_idempotent_stays_in_its_prefix_and_reverses(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'fresh.db'}")
    async with engine.begin() as conn:
        assert await run_plugin_migrations(conn, [MANIFEST]) == {}
        assert await run_plugin_migrations(conn, [MANIFEST]) == {}                    # a second boot changes nothing
        await v001_tables.up(conn)                                                    # and `up` itself is re-runnable
        tables = {r[0] for r in (await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))).fetchall()}
        assert {"local_inv_materials", "local_inv_spools", "local_inv_weight_log"} <= tables
        assert (await conn.execute(text("SELECT count(*) FROM plugin_schema_versions WHERE plugin_id='local_inventory'"))).scalar_one() == 1
        await v001_tables.down(conn)
        left = {r[0] for r in (await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))).fetchall()}
        assert not {t for t in left if t.startswith("local_inv_")}
    await engine.dispose()


async def test_a_material_with_spools_cannot_be_deleted_but_deleting_a_spool_takes_its_log(session_factory, local):
    m = await local.create_material(MaterialDraft(name="PLA"))
    s = await local.create_spool(SpoolDraft(material_ref=m.ref, initial_g=500.0))
    async with session_factory() as db:
        await db.execute(text("PRAGMA foreign_keys=ON"))
        with pytest.raises(IntegrityError):
            await db.execute(text("DELETE FROM local_inv_materials WHERE id = :i"), {"i": int(m.ref)})
        await db.rollback()
        await db.execute(text("PRAGMA foreign_keys=ON"))
        await db.execute(text("DELETE FROM local_inv_spools WHERE id = :i"), {"i": int(s.ref)})
        await db.commit()
    assert await _rows(session_factory, "SELECT * FROM local_inv_weight_log") == []


# ---- weight, audit log, labels ----------------------------------------------------------------------------------------

async def test_set_remaining_updates_the_spool_and_appends_one_audit_row_each_time(session_factory, local):
    m = await local.create_material(MaterialDraft(name="PLA", vendor="Acme"))
    s = await local.create_spool(SpoolDraft(material_ref=m.ref, initial_g=1000.0))

    await local.set_remaining(s.ref, 640.5)
    await local.set_remaining(s.ref, 600.0)

    assert (await local.get_spool(s.ref)).remaining_g == 600.0
    log = await _rows(session_factory, "SELECT old_g, new_g, source FROM local_inv_weight_log ORDER BY id")
    assert log == [{"old_g": None, "new_g": 1000.0, "source": "create"}, {"old_g": 1000.0, "new_g": 640.5, "source": "set_remaining"},
                   {"old_g": 640.5, "new_g": 600.0, "source": "set_remaining"}]


async def test_a_failed_weight_change_changes_nothing(session_factory, local):
    m = await local.create_material(MaterialDraft(name="PLA"))
    s = await local.create_spool(SpoolDraft(material_ref=m.ref, initial_g=500.0))
    with pytest.raises(InventoryProviderError) as neg:
        await local.set_remaining(s.ref, -1.0)
    with pytest.raises(InventoryProviderError) as missing:
        await local.set_remaining("999", 10.0)
    with pytest.raises(InventoryProviderError) as junk:
        await local.set_remaining("abc", 10.0)
    assert (neg.value.status, missing.value.status, junk.value.status) == (422, 404, 404)

    async with session_factory() as db:                                       # the audit write fails -> the weight update rolls back too
        await db.execute(text("DROP TABLE local_inv_weight_log"))
        await db.commit()
    with pytest.raises(Exception):
        await local.set_remaining(s.ref, 100.0)
    assert (await local.get_spool(s.ref)).remaining_g == 500.0


@pytest.mark.parametrize("text_,ref", [("themis:s-12", "12"), ("THEMIS:S-7", "7"), (" s-345 ", "345"), ("42", "42"),
                                       ("web+spoolman:s-9", None), ("themis:f-3", None), ("", None), ("12 34", None), ("hello", None)])
def test_label_formats(text_, ref):
    assert LocalInventoryProvider(LocalInventorySettings()).parse_label(text_) == ref


async def test_new_spools_default_to_the_configured_weight_and_explicit_values_win(session_factory):
    p = LocalInventoryProvider(LocalInventorySettings(default_initial_g=750))
    m = await p.create_material(MaterialDraft(name="PETG"))
    plain = await p.create_spool(SpoolDraft(material_ref=m.ref))
    sized = await p.create_spool(SpoolDraft(material_ref=m.ref, initial_g=250.0))
    partial = await p.create_spool(SpoolDraft(material_ref=m.ref, initial_g=250.0, remaining_g=100.0))
    assert [(x.initial_g, x.remaining_g) for x in (plain, sized, partial)] == [(750.0, 750.0), (250.0, 250.0), (250.0, 100.0)]
    assert plain.label == "PETG"                                              # label derived from vendor + name


async def test_non_numeric_refs_are_unknown_not_errors_from_the_database(local):
    assert await local.get_spool("abc") is None
    for call in (local.update_spool("x", {"location": "a"}), local.archive_material("x"), local.set_profile_links("x", {})):
        with pytest.raises(InventoryProviderError) as e:
            await call
        assert e.value.status == 404


# ---- the plugin's own route --------------------------------------------------------------------------------------------

async def test_the_weight_log_route_lists_newest_first_filters_by_spool_and_needs_the_inventory_scope(client, session_factory, local):
    from tests.api.test_inventory_api import _client_with
    m = await local.create_material(MaterialDraft(name="PLA"))
    a = await local.create_spool(SpoolDraft(material_ref=m.ref, initial_g=500.0))
    b = await local.create_spool(SpoolDraft(material_ref=m.ref, initial_g=300.0))
    await local.set_remaining(a.ref, 400.0)

    everything = (await client.get(f"{PLUGIN}/weight-log")).json()
    assert [(r["spool_ref"], r["new_g"], r["source"]) for r in everything] == [(a.ref, 400.0, "set_remaining"), (b.ref, 300.0, "create"), (a.ref, 500.0, "create")]
    only_b = (await client.get(f"{PLUGIN}/weight-log", params={"spool_ref": b.ref})).json()
    assert [r["spool_ref"] for r in only_b] == [b.ref]
    assert len((await client.get(f"{PLUGIN}/weight-log", params={"limit": 1})).json()) == 1
    assert (await client.get(f"{PLUGIN}/weight-log", params={"spool_ref": "nope"})).json() == []

    narrow = await _client_with(session_factory, ["queue:read"])
    async with narrow:
        assert (await narrow.get(f"{PLUGIN}/weight-log")).status_code == 403


# ---- as the active provider ------------------------------------------------------------------------------------------

async def test_the_whole_library_lifecycle_works_through_the_neutral_api_with_local_active(client):
    await _active(client)
    assert (await client.get(f"{BASE}/sync-status")).json()["capabilities"] == sorted(MANIFEST.provides[CAPABILITY].features)

    material = (await client.post(f"{BASE}/materials", json={"name": "PLA Red", "material": "PLA", "vendor": "Acme", "color_hex": "ff0000"})).json()
    assert (material["ref"], material["color_hex"], material["archived"]) == ("1", "#FF0000", False)
    spool = (await client.post(f"{BASE}/spools", json={"material_ref": material["ref"], "initial_g": 1000, "location": "Shelf A"})).json()
    assert (spool["ref"], spool["remaining_g"], spool["label"], spool["location"]) == ("1", 1000.0, "Acme PLA Red", "Shelf A")

    assert (await client.put(f"{BASE}/spools/1/remaining", json={"remaining_g": 412.5})).json()["remaining_g"] == 412.5
    assert (await client.patch(f"{BASE}/spools/1", json={"location": "Drawer 3"})).json()["location"] == "Drawer 3"
    assert (await client.patch(f"{BASE}/materials/1/profile-links", json={"links": {"Printer A": ["PLA @A"]}})).json()["profile_links"] == {"Printer A": ["PLA @A"]}
    assert (await client.post(f"{BASE}/resolve-label", json={"text": "themis:s-1"})).json() == {"spool_ref": "1"}

    listed = (await client.get(f"{BASE}/spools")).json()
    assert (listed["stale"], listed["provider"]) == (False, "local_inventory")           # never cached, never stale
    (item,) = listed["items"]
    assert (item["remaining_g"], item["unsynced"], item["material"]["profile_links"]) == (412.5, False, {"Printer A": ["PLA @A"]})

    await client.post(f"{BASE}/spools/1/archive")
    assert (await client.get(f"{BASE}/spools")).json()["items"] == []                     # archived: hidden by default
    assert len((await client.get(f"{BASE}/spools", params={"include_archived": True})).json()["items"]) == 1
    from app.services.inventory import cache
    assert await cache.load("local_inventory", "spools") is None                           # not REMOTE: nothing cached


async def test_low_stock_preflight_and_effective_remaining_use_it_like_any_provider(client, session_factory, local):
    await _active(client)
    m = await local.create_material(MaterialDraft(name="PLA"))
    s = await local.create_spool(SpoolDraft(material_ref=m.ref, initial_g=1000.0, remaining_g=60.0))
    assert (await client.put(f"{BASE}/settings", json={"low_stock": {"default_g": 100, "overrides": {}}})).status_code == 200

    assert (await read.spools_by_ref())[s.ref].remaining_g == 60.0
    from app.services.inventory import preflight
    warning = preflight.check_spool_sufficiency(150.0, (await read.spools_by_ref())[s.ref])
    assert warning and warning["spool_ref"] == s.ref and warning["remaining_g"] == 60.0

    async with session_factory() as db:
        from app.services.inventory import alerts
        low = await alerts.process(db, "local_inventory", await local.list_spools())
        await db.commit()
    assert [l.spool_ref for l in low] == [s.ref]


async def test_deduction_flows_through_the_outbox_into_the_audit_log(client, session_factory, local):
    """Snapshot -> absolute write -> the Local audit log, with no REMOTE machinery involved."""
    from app.models import Job, Printer, UploadedFile
    await _active(client)
    m = await local.create_material(MaterialDraft(name="PLA"))
    s = await local.create_spool(SpoolDraft(material_ref=m.ref, initial_g=500.0))
    async with session_factory() as db:
        db.add(Printer(id=1, name="P", printer_type="elegoo_centauri", connection_config={}))
        f = UploadedFile(original_filename="a", stored_path="/a", plates=[], uploaded_at="2026-01-01T00:00:00+00:00")
        db.add(f)
        await db.flush()
        job = Job(uploaded_file_id=f.id, plate_number=1, queue_position=1.0, status="printing",
                  created_at="2026-01-01T00:00:00+00:00", updated_at="2026-01-01T00:00:00+00:00")
        db.add(job)
        await db.commit()
        job_id = job.id
    await snapshots.take(session_factory, job_id, 1, s.ref)

    async with session_factory() as db:
        plan = await deduction.plan_completion(db, job=await db.get(Job, job_id), printer_id=1, spool_ref=s.ref, grams=37.5, source="queue")
        await db.commit()
    deduction.after_commit(plan, session_factory)
    await tasks.drain()

    assert (await local.get_spool(s.ref)).remaining_g == 462.5
    log = await _rows(session_factory, "SELECT new_g, source FROM local_inv_weight_log ORDER BY id")
    assert log[-1] == {"new_g": 462.5, "source": "set_remaining"}
    assert [r["status"] for r in await _rows(session_factory, "SELECT status FROM inventory_pending_writes")] == ["applied"]


async def test_switching_away_removes_the_provider_but_keeps_its_data(client, session_factory, local):
    await _active(client)
    m = await local.create_material(MaterialDraft(name="PLA"))
    await client.put(f"/api/v1/capabilities/{CAPABILITY}/provider", json={"plugin_id": None})
    assert (await client.get(f"{BASE}/materials")).status_code == 409
    assert len(await _rows(session_factory, "SELECT * FROM local_inv_materials")) == 1          # data stays for next time
    assert plugin_host.active(CAPABILITY) is None and m.ref == "1"


@pytest.mark.parametrize("ref", ["²", "٣", "9" * 30, "-1", "1.5", " 1", ""])
async def test_malformed_refs_are_unknown_never_an_uncontained_error(local, client, ref):
    assert await local.get_spool(ref) is None
    for call in (local.set_remaining(ref, 1.0), local.update_spool(ref, {"location": "x"}), local.archive_spool(ref)):
        with pytest.raises(InventoryProviderError) as e:
            await call
        assert e.value.status == 404
    assert (await client.get(f"{PLUGIN}/weight-log", params={"spool_ref": ref})).json() == []


@pytest.mark.parametrize("label", ["s-²", "themis:s-" + "9" * 30, "٣"])
def test_labels_with_unicode_digits_or_overflowing_ids_are_not_labels(label):
    assert LocalInventoryProvider(LocalInventorySettings()).parse_label(label) is None


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), -0.5])
async def test_weights_must_be_finite_and_non_negative(local, session_factory, bad):
    m = await local.create_material(MaterialDraft(name="PLA"))
    s = await local.create_spool(SpoolDraft(material_ref=m.ref, initial_g=500.0))
    for call in (local.set_remaining(s.ref, bad), local.create_spool(SpoolDraft(material_ref=m.ref, initial_g=bad)),
                 local.create_spool(SpoolDraft(material_ref=m.ref, initial_g=500.0, remaining_g=bad))):
        with pytest.raises(InventoryProviderError) as e:
            await call
        assert e.value.status == 422
    assert (await local.get_spool(s.ref)).remaining_g == 500.0                       # nothing stored, nothing logged
    assert len(await _rows(session_factory, "SELECT * FROM local_inv_weight_log")) == 1
