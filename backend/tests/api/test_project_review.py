"""BIZ-363: Project Builder Review checks, the X/Y/Z fit check, printer bed height, and adding an item to a chosen group."""
from unittest.mock import patch

from app.services.providers import contract as c
from tests.fake_providers import FakeProjectConstructor


class Measurer(FakeProjectConstructor):
    """`measure` sizes parts by file name: {name: (x, y, z)}; anything else is unmeasurable."""
    def __init__(self, sizes):
        super().__init__()
        self.sizes = sizes
        self.calls = 0

    def measure(self, path, selection):
        self.calls += 1
        return [self.sizes.get(path.name.split("_", 1)[-1]) or self.sizes.get(path.name) for _ in selection]


async def _project(client, upload_3mf, targets, files, stage="planning"):
    pid = (await client.post("/api/v1/projects", json={"name": "Kit", "stage": stage})).json()["id"]
    (g,) = (await client.get(f"/api/v1/projects/{pid}/groups")).json()
    await client.patch(f"/api/v1/projects/{pid}/groups/{g['id']}", json={"targets": targets})
    ids = {}
    for name in files:
        fid = await upload_3mf(filename=name, data=f"solid {name}\nendsolid\n".encode())
        ids[name] = (await client.post(f"/api/v1/projects/{pid}/items", json={"file_id": fid, "quantity": 1})).json()["id"]
    return pid, g["id"], ids


async def _review(client, pid, measurer, tmp_path):
    with patch("app.config.get_library_dir", return_value=tmp_path / "library"), \
         patch("app.api.routes.projects.get_project_constructor", return_value=measurer):
        r = await client.get(f"/api/v1/projects/{pid}/review")
    assert r.status_code == 200, r.text
    return r.json()


def _levels(body, level):
    return [ch["message"] for ch in body["checks"] if ch["level"] == level]


async def test_a_part_too_big_for_every_eligible_bed_is_an_error_that_blocks_queueing(client, upload_3mf, create_printer, tmp_path):
    a = await create_printer(name="Small", bed_x_mm=180.0, bed_y_mm=180.0, bed_z_mm=180.0)
    pid, gid, ids = await _project(client, upload_3mf, [{"target_key": str(a), "process_profile": "0.2"}], ["big.stl"])
    body = await _review(client, pid, Measurer({"big.stl": (200.0, 50.0, 20.0)}), tmp_path)
    (err,) = [ch for ch in body["checks"] if ch["level"] == "error"]
    assert body["open"] == 1 and err["tab"] == "items" and err["item_id"] == ids["big.stl"] and err["group_id"] == gid
    assert "200 × 50 × 20 mm" in err["message"] and "fits none" in err["message"]


async def test_a_part_that_fits_some_printers_is_a_note_and_rotation_counts(client, upload_3mf, create_printer, tmp_path):
    small = await create_printer(name="Small", bed_x_mm=180.0, bed_y_mm=180.0, bed_z_mm=180.0)
    wide = await create_printer(name="Wide", bed_x_mm=100.0, bed_y_mm=300.0, bed_z_mm=300.0)
    pid, _, _ = await _project(client, upload_3mf, [{"target_key": str(small), "process_profile": "0.2"},
                                                     {"target_key": str(wide), "process_profile": "0.2"}], ["rail.stl"])
    body = await _review(client, pid, Measurer({"rail.stl": (250.0, 40.0, 10.0)}), tmp_path)     # fits Wide turned 90°
    assert body["open"] == 0
    assert any("too big for Small; the others can take it" in m for m in _levels(body, "note"))


async def test_the_z_check_uses_the_printer_height_then_the_machine_profile_then_says_unknown(
        client, upload_3mf, create_printer, tmp_path):
    a = await create_printer(name="Short", bed_x_mm=256.0, bed_y_mm=256.0, bed_z_mm=150.0)
    pid, _, _ = await _project(client, upload_3mf, [{"target_key": str(a), "process_profile": "0.2"}], ["tall.stl"])
    m = Measurer({"tall.stl": (50.0, 50.0, 200.0)})
    assert (await _review(client, pid, m, tmp_path))["open"] == 1                                # too tall for 150 mm
    await client.patch(f"/api/v1/printers/{a}", json={"bed_z_mm": None})                           # unknown: X/Y only + a note
    body = await _review(client, pid, m, tmp_path)
    assert body["open"] == 0 and any("Build height unknown for Short" in n for n in _levels(body, "note"))
    from app.services.providers.slicing import Catalog, Preset
    catalog = Catalog(machines=[Preset("m1", "Bambu Lab P1S 0.4", height_mm=256.0)])
    with patch("app.services.catalog_service.cached_catalog", return_value=catalog):
        body = await _review(client, pid, m, tmp_path)                                              # the profile's 256 mm fits
    assert body["open"] == 0 and not any("Build height unknown" in n for n in _levels(body, "note"))
    with patch("app.services.catalog_service.cached_catalog",
               return_value=Catalog(machines=[Preset("m1", "Bambu Lab P1S 0.4", height_mm=180.0)])):
        assert (await _review(client, pid, m, tmp_path))["open"] == 1


async def test_printer_default_on_a_printer_without_one_is_an_error_naming_it(
        client, upload_3mf, create_printer, session_factory, tmp_path):
    from app.models import Printer
    a = await create_printer(name="NoDefault")
    b = await create_printer(name="HasDefault")
    async with session_factory() as s:
        (await s.get(Printer, b)).default_process_profile = "0.20mm Standard @BBL P1S"
        await s.commit()
    pid, gid, _ = await _project(client, upload_3mf, [{"target_key": "any", "process_profile": ""}], ["part.stl"])
    body = await _review(client, pid, Measurer({"part.stl": (10.0, 10.0, 10.0)}), tmp_path)
    errs = [ch for ch in body["checks"] if ch["level"] == "error"]
    assert len(errs) == 1 and errs[0]["tab"] == "groups" and errs[0]["group_id"] == gid
    assert "NoDefault has no default process profile" in errs[0]["message"] and "HasDefault" not in errs[0]["message"]
    assert a and b


async def test_a_group_without_printers_and_a_draft_are_errors(client, upload_3mf, tmp_path):
    pid, gid, _ = await _project(client, upload_3mf, [], ["part.stl"], stage="draft")
    body = await _review(client, pid, Measurer({}), tmp_path)
    tabs = sorted(ch["tab"] for ch in body["checks"] if ch["level"] == "error")
    assert tabs == ["details", "groups"] and body["open"] == 2


async def test_an_unmeasurable_part_is_a_note_and_sizes_are_cached_per_part(client, upload_3mf, create_printer, tmp_path):
    a = await create_printer(name="A", bed_z_mm=250.0)
    pid, _, _ = await _project(client, upload_3mf, [{"target_key": str(a), "process_profile": "0.2"}], ["odd.stl", "ok.stl"])
    m = Measurer({"ok.stl": (10.0, 10.0, 10.0)})
    body = await _review(client, pid, m, tmp_path)
    assert body["open"] == 0 and any("odd.stl: its size is unknown" in n for n in _levels(body, "note"))
    calls = m.calls
    await _review(client, pid, m, tmp_path)
    assert m.calls == calls + 1                    # only the unmeasured part is asked again; ok.stl came from the cache


async def test_a_provider_that_cannot_measure_never_fails_the_review(client, upload_3mf, create_printer, tmp_path):
    a = await create_printer(name="A")
    pid, _, _ = await _project(client, upload_3mf, [{"target_key": str(a), "process_profile": "0.2"}], ["part.stl"])
    body = await _review(client, pid, FakeProjectConstructor(), tmp_path)          # the contract default: unsupported
    assert body["open"] == 0 and any("size is unknown" in n for n in _levels(body, "note"))


async def test_no_loaded_filament_for_an_item_is_a_note(client, upload_3mf, create_printer, tmp_path):
    a = await create_printer(name="A", loaded_filaments=[{"slot": 0, "type": "PETG", "subtype": "standard", "color": "#000000"}])
    pid = (await client.post("/api/v1/projects", json={"name": "Kit", "stage": "planning"})).json()["id"]
    (g,) = (await client.get(f"/api/v1/projects/{pid}/groups")).json()
    await client.patch(f"/api/v1/projects/{pid}/groups/{g['id']}", json={"targets": [{"target_key": str(a), "process_profile": "x"}]})
    fid = await upload_3mf(filename="pla.stl", data=b"solid pla\nendsolid\n")
    await client.post(f"/api/v1/projects/{pid}/items", json={"file_id": fid, "filament_type": "PLA", "filament_subtype": "any"})
    body = await _review(client, pid, Measurer({"pla.stl": (1.0, 1.0, 1.0)}), tmp_path)
    assert any("no eligible printer has its filament loaded" in n for n in _levels(body, "note")) and body["open"] == 0


async def test_an_item_can_be_added_straight_into_a_group(client, upload_3mf):
    pid = (await client.post("/api/v1/projects", json={"name": "Kit"})).json()["id"]
    other = (await client.post(f"/api/v1/projects/{pid}/groups", json={"name": "Fine"})).json()["id"]
    fid = await upload_3mf(filename="part.stl", data=b"solid part\nendsolid\n")
    r = await client.post(f"/api/v1/projects/{pid}/items", json={"file_id": fid, "group_id": other})
    assert r.status_code in (200, 201) and r.json()["group_id"] == other
    p2 = (await client.post("/api/v1/projects", json={"name": "Else"})).json()["id"]
    foreign = (await client.get(f"/api/v1/projects/{p2}/groups")).json()[0]["id"]
    r = await client.post(f"/api/v1/projects/{pid}/items", json={"file_id": fid, "group_id": foreign})
    assert r.status_code == 422


async def test_printer_bed_height_is_saved_and_cleared(client, create_printer):
    a = await create_printer(name="A", bed_z_mm=250.0)
    assert (await client.get(f"/api/v1/printers/{a}")).json()["bed_z_mm"] == 250.0
    await client.patch(f"/api/v1/printers/{a}", json={"bed_z_mm": 300.0})
    assert (await client.get(f"/api/v1/printers/{a}")).json()["bed_z_mm"] == 300.0
    await client.patch(f"/api/v1/printers/{a}", json={"name": "A2"})                               # unsent: kept
    assert (await client.get(f"/api/v1/printers/{a}")).json()["bed_z_mm"] == 300.0
    await client.patch(f"/api/v1/printers/{a}", json={"bed_z_mm": None})
    assert (await client.get(f"/api/v1/printers/{a}")).json()["bed_z_mm"] is None
    assert (await client.patch(f"/api/v1/printers/{a}", json={"bed_z_mm": 0})).status_code == 422
    assert c.ProviderError                                                                          # (contract import used above)
