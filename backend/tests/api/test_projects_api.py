import io
from tests.fake_providers import fake_packer
import zipfile
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


def _make_stl_bytes() -> bytes:
    return b"solid part\nendsolid"


def _make_3mf_bytes(plate_count: int = 1) -> bytes:
    """Minimal .3mf (zip) with plate thumbnails so _parse_plate_nums returns correct IDs."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for i in range(1, plate_count + 1):
            zf.writestr(f"Metadata/plate_{i}.png", b"\x89PNG\r\n\x1a\n")
    return buf.getvalue()


async def _setup_project_with_stl(client, tmp_path) -> tuple[int, int]:
    """Create a project, upload an STL, add it as a project item. Returns (project_id, file_id)."""
    lib = tmp_path / "library"
    lib.mkdir(exist_ok=True)
    (tmp_path / "filecache").mkdir(exist_ok=True)

    with (
        patch("app.config.get_library_dir", return_value=lib),
        patch("app.config.get_filecache_dir", return_value=tmp_path / "filecache"),
    ):
        proj_resp = await client.post("/api/v1/projects", json={"name": "Test Pack"})
        assert proj_resp.status_code == 201
        project_id = proj_resp.json()["id"]

        upload_resp = await client.post(
            "/api/v1/files/upload",
            files={"file": ("part.stl", _make_stl_bytes(), "application/octet-stream")},
        )
        assert upload_resp.status_code == 201
        file_id = upload_resp.json()["id"]

        item_resp = await client.post(
            f"/api/v1/projects/{project_id}/items",
            json={"file_id": file_id, "quantity": 1},
        )
        assert item_resp.status_code == 201

    return project_id, file_id


async def test_create_project_without_machine_process(client):
    """Projects can be created without machine_uuid / process_uuid for external importers."""
    resp = await client.post(
        "/api/v1/projects",
        json={"name": "Ordinus Import"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Ordinus Import"
    assert data["source_app"] is None


async def test_create_project_with_source_fields(client):
    """Source fields are stored and returned on the project."""
    resp = await client.post(
        "/api/v1/projects",
        json={
            "name": "Ordinus Import",
            "source_app": "ordinus",
            "source_user": "alice",
            "source_layout_id": 42,
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["source_app"] == "ordinus"
    assert data["source_user"] == "alice"
    assert data["source_layout_id"] == 42


async def test_create_project_source_fields_default_null(client):
    """Source fields are null when not provided."""
    resp = await client.post(
        "/api/v1/projects",
        json={"name": "Regular Project", "machine_uuid": "abc", "process_uuid": "def"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["source_app"] is None
    assert data["source_user"] is None


async def test_create_project_payment_defaults(client):
    resp = await client.post("/api/v1/projects", json={"name": "Widget batch"})
    assert resp.status_code == 201
    data = resp.json()
    assert data["amount_paid"] is None
    assert data["payment_status"] == "unpaid"
    assert data["filament_cost_total"] is None


async def test_create_project_with_payment(client):
    resp = await client.post("/api/v1/projects", json={
        "name": "Widget batch", "amount_paid": 20.0, "payment_status": "partial",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["amount_paid"] == 20.0
    assert data["payment_status"] == "partial"


async def test_project_invalid_payment_status_rejected(client):
    resp = await client.post("/api/v1/projects", json={
        "name": "Widget batch", "payment_status": "not-a-status",
    })
    assert resp.status_code == 422
    assert (await client.get("/api/v1/projects")).json() == []  # no project was created


async def test_patch_project_payment_status(client):
    project_id = (await client.post("/api/v1/projects", json={"name": "Widget batch"})).json()["id"]
    resp = await client.patch(f"/api/v1/projects/{project_id}", json={
        "amount_paid": 15.5, "payment_status": "paid",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["amount_paid"] == 15.5
    assert data["payment_status"] == "paid"
    assert data["source_layout_id"] is None


async def test_generate_stores_3mf_in_job_pack_folder(client, tmp_path):
    """generate stores the 3MF under Job Pack 3MFs/<job_id>/ in the library."""
    project_id, _ = await _setup_project_with_stl(client, tmp_path)

    lib = tmp_path / "library"
    fake_3mf = _make_3mf_bytes(plate_count=1)

    with (
        patch("app.config.get_library_dir", return_value=lib),
        patch("app.config.get_filecache_dir", return_value=tmp_path / "filecache"),
        patch("app.api.routes.projects.get_project_constructor") as mock_get,
    ):
        mock_get.return_value = fake_packer(fake_3mf)

        resp = await client.post(
            f"/api/v1/projects/{project_id}/generate",
            json={"eligible_printer_ids": []},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["files"]) == 1
    assert len(data["jobs"]) == 1

    job_id = data["jobs"][0]["id"]
    file_info = data["files"][0]

    assert file_info["folder"] == f"/Job Pack 3MFs/{job_id}"

    job_pack_dir = lib / "Job Pack 3MFs" / str(job_id)
    assert job_pack_dir.is_dir()
    packed_files = list(job_pack_dir.iterdir())
    assert len(packed_files) == 1
    assert packed_files[0].suffix == ".3mf"


async def test_generate_3mf_folder_field_in_db(client, tmp_path):
    """UploadedFile.folder is set to /Job Pack 3MFs/<job_id> in the database."""
    project_id, _ = await _setup_project_with_stl(client, tmp_path)

    lib = tmp_path / "library"
    fake_3mf = _make_3mf_bytes(plate_count=1)

    with (
        patch("app.config.get_library_dir", return_value=lib),
        patch("app.config.get_filecache_dir", return_value=tmp_path / "filecache"),
        patch("app.api.routes.projects.get_project_constructor") as mock_get,
    ):
        mock_get.return_value = fake_packer(fake_3mf)

        resp = await client.post(
            f"/api/v1/projects/{project_id}/generate",
            json={"eligible_printer_ids": []},
        )

    assert resp.status_code == 200
    data = resp.json()
    job_id = data["jobs"][0]["id"]
    expected_folder = f"/Job Pack 3MFs/{job_id}"

    # Verify DB record via the list endpoint filtered by the expected folder.
    list_resp = await client.get(f"/api/v1/files", params={"folder": expected_folder})
    assert list_resp.status_code == 200
    files_in_folder = list_resp.json()
    assert len(files_in_folder) == 1
    assert files_in_folder[0]["folder"] == expected_folder


async def test_generate_multi_plate_uses_id_range_subfolder(client, tmp_path):
    """A multi-plate 3MF creates jobs whose IDs form the subfolder (first_id-last_id)."""
    project_id, _ = await _setup_project_with_stl(client, tmp_path)

    lib = tmp_path / "library"
    fake_3mf = _make_3mf_bytes(plate_count=2)

    with (
        patch("app.config.get_library_dir", return_value=lib),
        patch("app.config.get_filecache_dir", return_value=tmp_path / "filecache"),
        patch("app.api.routes.projects.get_project_constructor") as mock_get,
    ):
        mock_get.return_value = fake_packer(fake_3mf)

        resp = await client.post(
            f"/api/v1/projects/{project_id}/generate",
            json={"eligible_printer_ids": []},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["jobs"]) == 2

    first_id = data["jobs"][0]["id"]
    last_id = data["jobs"][-1]["id"]
    expected_label = f"{first_id}-{last_id}"

    assert data["files"][0]["folder"] == f"/Job Pack 3MFs/{expected_label}"

    job_pack_dir = lib / "Job Pack 3MFs" / expected_label
    assert job_pack_dir.is_dir()


async def test_generate_printer_configs_use_process_preset_not_machine_preset(client, tmp_path, create_printer):
    """JobPrinterConfig.print_profile must be the user-supplied process_preset —
    never machine_profile_override (a machine preset, wrong namespace entirely)."""
    project_id, _ = await _setup_project_with_stl(client, tmp_path)
    printer_id = await create_printer()

    lib = tmp_path / "library"
    fake_3mf = _make_3mf_bytes(plate_count=1)

    with (
        patch("app.config.get_library_dir", return_value=lib),
        patch("app.config.get_filecache_dir", return_value=tmp_path / "filecache"),
        patch("app.api.routes.projects.get_project_constructor") as mock_get,
        patch("app.api.routes.jobs.queue_engine"),
    ):
        mock_get.return_value = fake_packer(fake_3mf)

        resp = await client.post(
            f"/api/v1/projects/{project_id}/generate",
            json={"eligible_printer_ids": [printer_id], "process_preset": "0.20mm Standard"},
        )

    assert resp.status_code == 200
    job_id = resp.json()["jobs"][0]["id"]

    detail = await client.get(f"/api/v1/jobs/{job_id}/details")
    cfg = detail.json()["printer_configs"][0]
    assert cfg["print_profile"] == "0.20mm Standard"
    assert cfg["print_profile"] != "Bambu Lab P1S 0.4"  # never the machine preset


async def test_generate_without_process_preset_does_not_invent_one(client, tmp_path, create_printer):
    """Omitting process_preset leaves print_profile unset (empty) — no silent default,
    no machine-preset fallback. The job fails cleanly at slice time instead."""
    project_id, _ = await _setup_project_with_stl(client, tmp_path)
    printer_id = await create_printer()

    lib = tmp_path / "library"
    fake_3mf = _make_3mf_bytes(plate_count=1)

    with (
        patch("app.config.get_library_dir", return_value=lib),
        patch("app.config.get_filecache_dir", return_value=tmp_path / "filecache"),
        patch("app.api.routes.projects.get_project_constructor") as mock_get,
        patch("app.api.routes.jobs.queue_engine"),
    ):
        mock_get.return_value = fake_packer(fake_3mf)

        resp = await client.post(
            f"/api/v1/projects/{project_id}/generate",
            json={"eligible_printer_ids": [printer_id]},
        )

    assert resp.status_code == 200
    job_id = resp.json()["jobs"][0]["id"]

    detail = await client.get(f"/api/v1/jobs/{job_id}/details")
    cfg = detail.json()["printer_configs"][0]
    assert cfg["print_profile"] == ""


async def test_generate_carries_filament_requirement_to_printer_config(client, tmp_path, create_printer):
    """The project item's filament_type/filament_color/filament_id must reach the
    generated JobPrinterConfig verbatim — otherwise the mismatch gate no-ops and
    parts print in whatever is loaded, ignoring the requirement."""
    lib = tmp_path / "library"
    lib.mkdir(exist_ok=True)
    (tmp_path / "filecache").mkdir(exist_ok=True)

    with (
        patch("app.config.get_library_dir", return_value=lib),
        patch("app.config.get_filecache_dir", return_value=tmp_path / "filecache"),
    ):
        proj_resp = await client.post("/api/v1/projects", json={"name": "Filament Req"})
        project_id = proj_resp.json()["id"]

        upload_resp = await client.post(
            "/api/v1/files/upload",
            files={"file": ("part.stl", _make_stl_bytes(), "application/octet-stream")},
        )
        file_id = upload_resp.json()["id"]

        item_resp = await client.post(
            f"/api/v1/projects/{project_id}/items",
            json={"file_id": file_id, "quantity": 1, "filament_type": "PETG", "filament_subtype": "any", "filament_color": "#FF0000"},
        )
        assert item_resp.status_code == 201

    printer_id = await create_printer()
    fake_3mf = _make_3mf_bytes(plate_count=1)

    with (
        patch("app.config.get_library_dir", return_value=lib),
        patch("app.config.get_filecache_dir", return_value=tmp_path / "filecache"),
        patch("app.api.routes.projects.get_project_constructor") as mock_get,
        patch("app.api.routes.jobs.queue_engine"),
    ):
        mock_get.return_value = fake_packer(fake_3mf)

        resp = await client.post(
            f"/api/v1/projects/{project_id}/generate",
            json={"eligible_printer_ids": [printer_id], "process_preset": "0.20mm"},
        )

    assert resp.status_code == 200
    job_id = resp.json()["jobs"][0]["id"]

    detail = await client.get(f"/api/v1/jobs/{job_id}/details")
    cfg = detail.json()["printer_configs"][0]
    assert (cfg["filament_manufacturer"], cfg["filament_type"], cfg["filament_subtype"], cfg["filament_color"]) \
        == (None, "PETG", "any", "#FF0000")


async def test_project_estimate_rollup_keys(client):
    """GET /projects/{id} response includes new rollup keys and excludes old ones."""
    resp = await client.post("/api/v1/projects", json={
        "name": "Test", "customer": "", "order_type": "internal",
        "on_hold": False, "due_date": None, "notes": None,
    })
    assert resp.status_code == 201
    proj_id = resp.json()["id"]

    get_resp = await client.get(f"/api/v1/projects/{proj_id}")
    data = get_resp.json()
    for key in ["estimate_filament_grams_total", "estimate_seconds_total",
                "estimate_filament_grams_remaining", "estimate_seconds_remaining",
                "actual_filament_grams", "actual_seconds"]:
        assert key in data, f"missing: {key}"
    assert "filament_grams" not in data
    assert "estimated_seconds" not in data


async def test_project_estimate_remaining_excludes_terminal_jobs(client, tmp_path, session_factory):
    """estimate_filament_grams_remaining excludes completed/cancelled/failed jobs."""
    from app.models import Job

    resp = await client.post("/api/v1/projects", json={
        "name": "P", "customer": "", "order_type": "internal",
        "on_hold": False, "due_date": None, "notes": None,
    })
    proj_id = resp.json()["id"]

    # Upload a file and create a printer so we can create a job
    with patch("app.config.get_library_dir", return_value=tmp_path / "library"), \
         patch("app.config.get_filecache_dir", return_value=tmp_path / "filecache"):
        (tmp_path / "library").mkdir(exist_ok=True)
        (tmp_path / "filecache").mkdir(exist_ok=True)
        import io as _io, zipfile as _zf
        buf = _io.BytesIO()
        with _zf.ZipFile(buf, "w") as zf:
            zf.writestr("Metadata/plate_1.png", b"\x89PNG")
        file_resp = await client.post(
            "/api/v1/files/upload",
            files={"file": ("m.3mf", buf.getvalue(), "application/octet-stream")},
        )
    file_id = file_resp.json()["id"]

    printer_resp = await client.post("/api/v1/printers", json={
        "name": "P1S", "printer_type": "bambu",
        "connection_config": {},
        "orca_printer_profiles": ["Bambu Lab P1S 0.4"],
        "machine_profile_override": "Bambu Lab P1S 0.4",
    })
    printer_id = printer_resp.json()["id"]

    with patch("app.api.routes.jobs.queue_engine") as mock_qe:
        mock_qe.spawn_estimate = MagicMock()
        mock_qe.wake = MagicMock()
        j1 = (await client.post("/api/v1/jobs", json={
            "uploaded_file_id": file_id, "plate_number": 1,
            "printer_configs": [{"printer_id": printer_id, "print_profile": "0.20mm", "filament_profile": "PLA", "filament_type": "any", "filament_color": "any"}]
        })).json()

    # Set project_id and estimates on job via DB override
    async with session_factory() as session:
        job = await session.get(Job, j1["id"])
        job.project_id = proj_id
        job.estimate_filament_grams = 10.0
        job.status = "complete"  # terminal — excluded from remaining
        await session.commit()

    detail = (await client.get(f"/api/v1/projects/{proj_id}")).json()
    assert detail["estimate_filament_grams_total"] == pytest.approx(10.0)
    assert detail["estimate_filament_grams_remaining"] is None  # all jobs terminal
    assert detail["actual_filament_grams"] is None


# ---------------------------------------------------------------------------
# Project parts (non-3D-printed hardware, e.g. magnets/screws)
# ---------------------------------------------------------------------------

async def _make_project(client) -> int:
    resp = await client.post("/api/v1/projects", json={"name": "Assembly"})
    assert resp.status_code == 201
    return resp.json()["id"]


async def test_add_part_defaults_and_appears_in_project(client):
    proj_id = await _make_project(client)

    resp = await client.post(
        f"/api/v1/projects/{proj_id}/parts",
        json={"name": "3mm magnet", "quantity": 5},
    )
    assert resp.status_code == 201
    part = resp.json()
    assert part["name"] == "3mm magnet"
    assert part["quantity"] == 5
    assert part["allocated"] is False
    assert part["project_id"] == proj_id

    detail = (await client.get(f"/api/v1/projects/{proj_id}")).json()
    assert len(detail["parts"]) == 1
    assert detail["parts"][0]["name"] == "3mm magnet"


async def test_add_part_rejects_nonpositive_quantity(client):
    proj_id = await _make_project(client)
    resp = await client.post(
        f"/api/v1/projects/{proj_id}/parts",
        json={"name": "M3 screw", "quantity": 0},
    )
    assert resp.status_code == 422
    assert (await client.get(f"/api/v1/projects/{proj_id}")).json()["parts"] == []  # no part created


async def test_add_part_404_for_missing_project(client):
    resp = await client.post(
        "/api/v1/projects/999999/parts",
        json={"name": "M3 screw", "quantity": 2},
    )
    assert resp.status_code == 404


async def test_update_part_toggles_allocated_flag(client):
    proj_id = await _make_project(client)
    part = (await client.post(
        f"/api/v1/projects/{proj_id}/parts",
        json={"name": "M3 screw", "quantity": 2},
    )).json()

    resp = await client.put(
        f"/api/v1/projects/{proj_id}/parts/{part['id']}",
        json={"allocated": True},
    )
    assert resp.status_code == 200
    updated = resp.json()
    assert updated["allocated"] is True
    # Untouched fields stay as-is
    assert updated["name"] == "M3 screw"
    assert updated["quantity"] == 2


async def test_update_part_404_for_wrong_project(client):
    proj_a = await _make_project(client)
    proj_b = await _make_project(client)
    part = (await client.post(
        f"/api/v1/projects/{proj_a}/parts",
        json={"name": "M3 screw", "quantity": 2},
    )).json()

    resp = await client.put(
        f"/api/v1/projects/{proj_b}/parts/{part['id']}",
        json={"allocated": True},
    )
    assert resp.status_code == 404


async def test_delete_part(client):
    proj_id = await _make_project(client)
    part = (await client.post(
        f"/api/v1/projects/{proj_id}/parts",
        json={"name": "M3 screw", "quantity": 2},
    )).json()

    resp = await client.delete(f"/api/v1/projects/{proj_id}/parts/{part['id']}")
    assert resp.status_code == 200
    assert resp.json() == {"deleted": part["id"]}

    detail = (await client.get(f"/api/v1/projects/{proj_id}")).json()
    assert detail["parts"] == []


async def test_generate_with_model_uuids_creates_model_targets_and_configs(
        client, tmp_path, create_printer, model_uuid_of, session_factory):
    """eligible_model_uuids -> a model target per job, materialized onto printers of that model only."""
    from sqlalchemy import select
    from app.models import JobModelTarget, JobPrinterConfig

    project_id, _ = await _setup_project_with_stl(client, tmp_path)
    p1s = await create_printer(name="A")  # a Bambu P1S
    await create_printer(name="B", printer_type="elegoo_centauri")
    model = await model_uuid_of(p1s)
    lib = tmp_path / "library"
    with (
        patch("app.config.get_library_dir", return_value=lib),
        patch("app.config.get_filecache_dir", return_value=tmp_path / "filecache"),
        patch("app.api.routes.projects.get_project_constructor") as mock_get,
    ):
        mock_get.return_value = fake_packer(_make_3mf_bytes(plate_count=1))
        resp = await client.post(
            f"/api/v1/projects/{project_id}/generate",
            json={"eligible_model_uuids": [model], "process_preset": "0.20mm"},
        )

    assert resp.status_code == 200, resp.text
    job_id = resp.json()["jobs"][0]["id"]
    async with session_factory() as s:
        targets = (await s.execute(select(JobModelTarget).where(JobModelTarget.job_id == job_id))).scalars().all()
        assert [(t.model_uuid, t.print_profile) for t in targets] == [(model, "0.20mm")]
        cfgs = (await s.execute(select(JobPrinterConfig).where(JobPrinterConfig.job_id == job_id))).scalars().all()
        assert [c.printer_id for c in cfgs] == [p1s]


async def _generate_with(client, tmp_path, provider, project_id):
    lib = tmp_path / "library"
    with (
        patch("app.config.get_library_dir", return_value=lib),
        patch("app.api.routes.projects.get_project_constructor", return_value=provider),
    ):
        return await client.post(f"/api/v1/projects/{project_id}/generate", json={"eligible_printer_ids": []})


async def test_generate_packs_each_library_file_once_with_its_summed_quantity_and_id(client, tmp_path):
    """One input per library file (quantity = sum over its project items, ref = the file id), not one path per copy."""
    from tests.fake_providers import fake_packer

    project_id, file_id = await _setup_project_with_stl(client, tmp_path)
    with patch("app.config.get_library_dir", return_value=tmp_path / "library"):
        assert (await client.post(f"/api/v1/projects/{project_id}/items", json={"file_id": file_id, "quantity": 3})).status_code == 201
    provider = fake_packer(_make_3mf_bytes(plate_count=1))
    resp = await _generate_with(client, tmp_path, provider, project_id)
    assert resp.status_code == 200, resp.text
    (req,) = provider.calls
    assert [(i.quantity, i.ref) for i in req.inputs] == [(4, str(file_id))]


async def test_generate_targets_the_bed_only_even_when_the_project_carries_preset_uuids(client, tmp_path, session_factory):
    """Themis applies profiles at dispatch: the project is always built for a bed size, never for presets."""
    from app.models import Project
    from app.services.providers.contract import TargetPrinter
    from tests.fake_providers import fake_packer

    project_id, _ = await _setup_project_with_stl(client, tmp_path)
    async with session_factory() as s:
        proj = await s.get(Project, project_id)
        proj.machine_uuid, proj.process_uuid = "machine-1", "process-1"
        await s.commit()
    provider = fake_packer(_make_3mf_bytes(plate_count=1))
    resp = await _generate_with(client, tmp_path, provider, project_id)
    assert resp.status_code == 200, resp.text
    (req,) = provider.calls
    assert req.target == TargetPrinter((256.0, 256.0, 250.0), None)   # no eligible printers: default bed, Laminus default z


async def test_generate_targets_the_smallest_eligible_bed_and_the_model_they_share(client, tmp_path, session_factory, create_printer):
    from app.models import Printer
    from tests.fake_providers import fake_packer

    project_id, _ = await _setup_project_with_stl(client, tmp_path)
    ids = [await create_printer(name="A"), await create_printer(name="B")]
    async with session_factory() as s:
        for pid, (bx, by) in zip(ids, ((300.0, 200.0), (250.0, 320.0))):
            p = await s.get(Printer, pid)
            p.bed_x_mm, p.bed_y_mm, p.model_uuid = bx, by, "model-a"
        await s.commit()

    async def target_for(printer_ids):
        provider = fake_packer(_make_3mf_bytes(plate_count=1))
        with patch("app.config.get_library_dir", return_value=tmp_path / "library"),              patch("app.api.routes.projects.get_project_constructor", return_value=provider):
            resp = await client.post(f"/api/v1/projects/{project_id}/generate",
                                     json={"eligible_printer_ids": printer_ids, "allow_cached": False})
        assert resp.status_code == 200, resp.text
        return provider.calls[0].target

    t = await target_for(ids)
    assert (t.bed_mm, t.model_uuid) == ((250.0, 200.0, 250.0), "model-a")
    async with session_factory() as s:
        (await s.get(Printer, ids[1])).model_uuid = "model-b"
        await s.commit()
    assert (await target_for(ids)).model_uuid is None                # two models: a bed-only target


@pytest.mark.parametrize("code, status, detail", [
    ("timeout", 504, "Generation timed out — try fewer parts or reduce quantities"),
    ("failed", 502, "Project builder error during generation: failed: pack returned 500: boom"),
    ("cancelled", 502, "Project builder error during generation: cancelled: pack returned 500: boom"),
    ("unreachable", 503, "The project builder is unavailable: unreachable: pack returned 500: boom"),
    ("not_ready", 503, "The project builder is unavailable: not_ready: pack returned 500: boom"),
    ("unsupported_input", 422, "The project builder cannot build this project: unsupported_input: pack returned 500: boom"),
])
async def test_generate_maps_constructor_errors_to_http(client, tmp_path, session_factory, code, status, detail):
    from sqlalchemy import select
    from app.models import Job, UploadedFile
    from app.services.providers.contract import ErrorCode, ProviderError
    from tests.fake_providers import FakeProjectConstructor

    project_id, _ = await _setup_project_with_stl(client, tmp_path)
    failing = FakeProjectConstructor(outputs=[ProviderError(ErrorCode(code), "pack returned 500: boom")])
    resp = await _generate_with(client, tmp_path, failing, project_id)
    assert (resp.status_code, resp.json()["detail"]) == (status, detail)
    async with session_factory() as s:
        assert (await s.execute(select(Job))).first() is None
        assert (await s.execute(select(UploadedFile).where(UploadedFile.pack_recipe_hash.is_not(None)))).first() is None


async def test_generate_422_without_a_project_builder(client, tmp_path):
    project_id, _ = await _setup_project_with_stl(client, tmp_path)
    none = await _generate_with(client, tmp_path, None, project_id)
    assert (none.status_code, none.json()["detail"]) == (
        422, "No project builder is configured — set up the plugin that provides 3mf.construct (Settings -> Plugins); "
             "it is required for generation")


async def test_generate_reuses_cached_packs_without_asking_a_down_project_builder(client, tmp_path):
    """A reusable pack needs no provider: regenerating while the builder is down still works and never probes it."""
    from app.services.providers.contract import Readiness
    from tests.fake_providers import FakeProjectConstructor, fake_packer

    project_id, _ = await _setup_project_with_stl(client, tmp_path)
    first = await _generate_with(client, tmp_path, fake_packer(_make_3mf_bytes(plate_count=1)), project_id)
    assert first.status_code == 200, first.text
    down = FakeProjectConstructor(Readiness.NOT_READY, status_detail="building catalog")
    again = await _generate_with(client, tmp_path, down, project_id)
    assert again.status_code == 200, again.text
    assert again.json()["files"][0]["pack_reused"] is True
    assert (down.status_calls, down.calls) == (0, [])


async def test_generate_checks_the_project_builder_once_per_request(client, tmp_path):
    from tests.fake_providers import fake_packer

    project_id, file_id = await _setup_project_with_stl(client, tmp_path)
    with patch("app.config.get_library_dir", return_value=tmp_path / "library"):   # a second group, a different recipe (same STL x1 would reuse the first pack)
        added = await client.post(f"/api/v1/projects/{project_id}/items", json={"file_id": file_id, "quantity": 2, "filament_type": "PETG", "filament_subtype": "any"})
        assert added.status_code == 201, added.text
    provider = fake_packer(_make_3mf_bytes(plate_count=1))
    resp = await _generate_with(client, tmp_path, provider, project_id)
    assert resp.status_code == 200, resp.text
    assert (provider.status_calls, len(provider.calls)) == (1, 2)


@pytest.mark.parametrize("state", ["not_ready", "unreachable"])
async def test_generate_503_and_builds_nothing_while_the_project_builder_is_not_ready(client, tmp_path, state):
    from app.services.providers.contract import Readiness
    from tests.fake_providers import FakeProjectConstructor

    project_id, _ = await _setup_project_with_stl(client, tmp_path)
    down = FakeProjectConstructor(Readiness(state), status_detail="health check returned 503")
    resp = await _generate_with(client, tmp_path, down, project_id)
    assert (resp.status_code, resp.json()["detail"]) == (503, "The project builder is unavailable: health check returned 503")
    assert down.status_calls == 1 and down.calls == []
    assert (await client.get(f"/api/v1/projects/{project_id}/jobs")).json() == []


async def test_generate_spawns_no_subprocess_and_keeps_the_packed_plates(client, tmp_path):
    """BIZ-284: pack output is read from its embedded PNGs only; nothing renders thumbnails via a local binary."""
    project_id, _ = await _setup_project_with_stl(client, tmp_path)
    lib = tmp_path / "library"
    boom = AssertionError("subprocess spawned")
    with (
        patch("app.config.get_library_dir", return_value=lib),
        patch("app.config.get_filecache_dir", return_value=tmp_path / "filecache"),
        patch("app.api.routes.projects.get_project_constructor") as mock_get,
        patch("subprocess.run", side_effect=boom), patch("subprocess.Popen", side_effect=boom),
    ):
        mock_get.return_value = fake_packer(_make_3mf_bytes(plate_count=2))
        resp = await client.post(f"/api/v1/projects/{project_id}/generate", json={"eligible_printer_ids": []})
    assert resp.status_code == 200
    assert len(resp.json()["jobs"]) == 2


def test_parse_plate_nums_uses_the_core_reader(tmp_path):
    """BIZ-283: the pack route shares plate discovery with the library scanner (PNG names, model_settings, StartPart)."""
    from app.api.routes.projects import _parse_plate_nums
    two = tmp_path / "two.3mf"
    two.write_bytes(_make_3mf_bytes(plate_count=2))
    assert _parse_plate_nums(two) == [1, 2]
    stripped = tmp_path / "s.3mf"
    with zipfile.ZipFile(stripped, "w") as z:
        z.writestr("Metadata/plate_1.png", b"x")
        z.writestr("Metadata/model_settings.config",
                   '<config><plate><metadata key="plater_id" value="1"/></plate><plate><metadata key="plater_id" value="2"/></plate></config>')
    assert _parse_plate_nums(stripped) == [1, 2]
    bad = tmp_path / "bad.3mf"
    bad.write_bytes(b"nope")
    assert _parse_plate_nums(bad) == []
