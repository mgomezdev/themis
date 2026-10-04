from unittest.mock import MagicMock, patch

import pytest


def _fake_gcode_with_estimates(output_dir, grams: float = 12.5, time_str: str = "1h 5m 30s"):
    output_dir.mkdir(parents=True, exist_ok=True)
    gcode_file = output_dir / "out.gcode"
    gcode_file.write_text(
        f"; filament used [g] = {grams}\n"
        f"; estimated printing time (normal mode) = {time_str}\n"
    )
    return str(gcode_file)


async def test_complete_manually_happy_path(client, tmp_path, session_factory, upload_3mf, create_printer, create_job):
    file_id = await upload_3mf()
    printer_id = await create_printer()
    job_id = await create_job(file_id, printer_id)

    mock_qe = MagicMock()
    mock_qe._slicer._data_dir = tmp_path

    async def fake_run_verify_slice(req, output_dir):
        return _fake_gcode_with_estimates(output_dir)

    mock_qe.run_verify_slice = fake_run_verify_slice

    with patch("app.api.routes.jobs.queue_engine", mock_qe), \
         patch("app.api.routes.jobs._deduct_spool") as mock_deduct:
        resp = await client.post(
            f"/api/v1/jobs/{job_id}/complete-manually", json={"printer_id": printer_id},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "complete"
    assert body["completed_at"] is not None
    assert body["actual_filament_grams"] == 12.5
    assert body["actual_seconds"] == 3930  # 1h5m30s
    assert body["assigned_printer_id"] == printer_id
    mock_deduct.assert_not_called()  # no spool loaded — nothing to deduct

    printer_resp = await client.get(f"/api/v1/printers/{printer_id}")
    printer_data = printer_resp.json()
    assert printer_data["awaiting_plate_clear"] is True

    # lifetime_job_count/lifetime_print_seconds aren't exposed via the printers
    # API - check the DB directly, same pattern as the 409-terminal-status test.
    from app.models import Job, Printer

    async with session_factory() as session:
        printer = await session.get(Printer, printer_id)
        assert printer.lifetime_job_count == 1
        assert printer.lifetime_print_seconds == 3930
        assert (await session.get(Job, job_id)).printed_on_printer_id == printer_id


async def test_complete_manually_404_unknown_job(client):
    resp = await client.post("/api/v1/jobs/999999/complete-manually", json={"printer_id": 1})
    assert resp.status_code == 404


async def test_complete_manually_404_unknown_printer(client, upload_3mf, create_printer, create_job):
    file_id = await upload_3mf()
    printer_id = await create_printer()
    job_id = await create_job(file_id, printer_id)

    resp = await client.post(
        f"/api/v1/jobs/{job_id}/complete-manually", json={"printer_id": 999999},
    )
    assert resp.status_code == 404


async def test_complete_manually_404_no_config_for_printer(client, upload_3mf, create_printer, create_job):
    file_id = await upload_3mf()
    printer_id = await create_printer()
    other_printer_id = await create_printer(name="P1S #2")
    job_id = await create_job(file_id, printer_id)

    resp = await client.post(
        f"/api/v1/jobs/{job_id}/complete-manually", json={"printer_id": other_printer_id},
    )
    assert resp.status_code == 404


async def test_complete_manually_409_already_complete(client, session_factory, upload_3mf, create_printer, create_job):
    from app.models import Job

    file_id = await upload_3mf()
    printer_id = await create_printer()
    job_id = await create_job(file_id, printer_id)

    async with session_factory() as session:
        job = await session.get(Job, job_id)
        job.status = "complete"
        await session.commit()

    resp = await client.post(
        f"/api/v1/jobs/{job_id}/complete-manually", json={"printer_id": printer_id},
    )
    assert resp.status_code == 409
    job = (await client.get(f"/api/v1/jobs/{job_id}")).json()  # the rejected call recorded nothing
    assert (job["status"], job["assigned_printer_id"]) == ("complete", None)
    assert (job["completed_at"], job["actual_seconds"], job["actual_filament_grams"]) == (None, None, None)


async def _set_job_status(session_factory, job_id, status, printer_id=None):
    from app.models import Job

    async with session_factory() as session:
        job = await session.get(Job, job_id)
        job.status = status
        job.assigned_printer_id = printer_id
        await session.commit()


async def test_complete_manually_slice_failure_422_leaves_job_untouched(client, tmp_path, session_factory, upload_3mf, create_printer, create_job):
    from app.services.slicer_service import SliceError

    file_id = await upload_3mf()
    printer_id = await create_printer()
    other_printer_id = await create_printer(name="P1S #2")
    job_id = await create_job(file_id, printer_id)
    # A genuinely printing job on another printer: a failed manual slice must not
    # orphan the live print from its printer.
    await _set_job_status(session_factory, job_id, "printing", other_printer_id)

    mock_qe = MagicMock()
    mock_qe._slicer._data_dir = tmp_path

    async def fake_run_verify_slice(req, output_dir):
        raise SliceError("OrcaSlicer exited with code 1\nboom")

    mock_qe.run_verify_slice = fake_run_verify_slice

    with patch("app.api.routes.jobs.queue_engine", mock_qe):
        resp = await client.post(
            f"/api/v1/jobs/{job_id}/complete-manually", json={"printer_id": printer_id},
        )

    assert resp.status_code == 422
    assert "OrcaSlicer" in resp.json()["detail"]

    details = (await client.get(f"/api/v1/jobs/{job_id}/details")).json()
    assert details["status"] == "printing"
    assert details["assigned_printer"]["id"] == other_printer_id
    assert details["block_reason"] is None


async def test_complete_manually_409_when_already_in_flight(client, upload_3mf, create_printer, create_job):
    from app.api.routes import jobs as jobs_route

    file_id = await upload_3mf()
    printer_id = await create_printer()
    job_id = await create_job(file_id, printer_id)

    jobs_route._manual_complete_in_flight.add(job_id)
    try:
        resp = await client.post(
            f"/api/v1/jobs/{job_id}/complete-manually", json={"printer_id": printer_id},
        )
    finally:
        jobs_route._manual_complete_in_flight.discard(job_id)
    assert resp.status_code == 409
    job = (await client.get(f"/api/v1/jobs/{job_id}")).json()  # the in-flight completion's job is untouched
    assert (job["status"], job["assigned_printer_id"], job["completed_at"]) == ("queued", None, None)


async def test_complete_manually_from_printing_status_completes(client, tmp_path, session_factory, upload_3mf, create_printer, create_job):
    file_id = await upload_3mf()
    printer_id = await create_printer()
    job_id = await create_job(file_id, printer_id)
    await _set_job_status(session_factory, job_id, "printing", printer_id)

    mock_qe = MagicMock()
    mock_qe._slicer._data_dir = tmp_path

    async def fake_run_verify_slice(req, output_dir):
        return _fake_gcode_with_estimates(output_dir)

    mock_qe.run_verify_slice = fake_run_verify_slice

    with patch("app.api.routes.jobs.queue_engine", mock_qe),          patch("app.api.routes.jobs._deduct_spool"):
        resp = await client.post(
            f"/api/v1/jobs/{job_id}/complete-manually", json={"printer_id": printer_id},
        )

    assert resp.status_code == 200
    assert resp.json()["status"] == "complete"


async def test_complete_manually_output_dir_cleaned_up_on_success(client, tmp_path, upload_3mf, create_printer, create_job):
    file_id = await upload_3mf()
    printer_id = await create_printer()
    job_id = await create_job(file_id, printer_id)

    mock_qe = MagicMock()
    mock_qe._slicer._data_dir = tmp_path
    expected_output_dir = tmp_path / "gcode_manual_complete" / str(job_id)

    async def fake_run_verify_slice(req, output_dir):
        assert output_dir == expected_output_dir
        return _fake_gcode_with_estimates(output_dir)

    mock_qe.run_verify_slice = fake_run_verify_slice

    with patch("app.api.routes.jobs.queue_engine", mock_qe), \
         patch("app.api.routes.jobs._deduct_spool"):
        resp = await client.post(
            f"/api/v1/jobs/{job_id}/complete-manually", json={"printer_id": printer_id},
        )

    assert resp.status_code == 200
    assert not expected_output_dir.exists()


async def test_complete_manually_sends_no_printer_commands(client, tmp_path, upload_3mf, create_printer, create_job):
    """The whole point of this endpoint is that it never talks to the printer.
    A mock vendor client is wired into printer_manager so any accidental call to
    start_print/upload_file/stop_print (or anything else on it) fails loudly."""
    file_id = await upload_3mf()
    printer_id = await create_printer()
    job_id = await create_job(file_id, printer_id)

    mock_qe = MagicMock()
    mock_qe._slicer._data_dir = tmp_path

    async def fake_run_verify_slice(req, output_dir):
        return _fake_gcode_with_estimates(output_dir)

    mock_qe.run_verify_slice = fake_run_verify_slice

    mock_client = MagicMock()
    mock_client.connected = True
    from app.services.printer_manager import printer_manager
    printer_manager._clients[printer_id] = mock_client
    try:
        with patch("app.api.routes.jobs.queue_engine", mock_qe), \
             patch("app.api.routes.jobs._deduct_spool"):
            resp = await client.post(
                f"/api/v1/jobs/{job_id}/complete-manually", json={"printer_id": printer_id},
            )
        assert resp.status_code == 200
        mock_client.start_print.assert_not_called()
        mock_client.upload_file.assert_not_called()
        mock_client.stop_print.assert_not_called()
        # orca_export_args (local file-naming, no printer I/O) IS expected to be
        # called by _build_slice_request - that's not a printer command.
    finally:
        printer_manager._clients.pop(printer_id, None)


async def test_complete_manually_deducts_spoolman_filament(client, tmp_path, upload_3mf, create_job, create_printer):
    file_id = await upload_3mf()
    printer_id = await create_printer(
        loaded_filaments=[{"slot": 0, "type": "PLA", "color": "#000000", "spoolman_spool_id": 42}],
    )
    job_id = await create_job(file_id, printer_id)

    await client.put("/api/v1/settings/spoolman", json={
        "enabled": True, "url": "http://spoolman.test",
    })

    mock_qe = MagicMock()
    mock_qe._slicer._data_dir = tmp_path

    async def fake_run_verify_slice(req, output_dir):
        return _fake_gcode_with_estimates(output_dir, grams=8.0)

    mock_qe.run_verify_slice = fake_run_verify_slice

    with patch("app.api.routes.jobs.queue_engine", mock_qe), \
         patch("app.api.routes.jobs._deduct_spool") as mock_deduct:
        resp = await client.post(
            f"/api/v1/jobs/{job_id}/complete-manually", json={"printer_id": printer_id},
        )

    assert resp.status_code == 200
    mock_deduct.assert_called_once()
    call_args = mock_deduct.call_args[0]
    from app.services.providers.spoolman import SpoolmanInventoryProvider
    assert isinstance(call_args[0], SpoolmanInventoryProvider)
    assert call_args[1] == 42                       # spool_id
    assert call_args[2] == 8.0                       # grams


async def test_complete_manually_skips_deduction_when_spoolman_disabled(client, tmp_path, upload_3mf, create_job, create_printer):
    file_id = await upload_3mf()
    printer_id = await create_printer(
        loaded_filaments=[{"slot": 0, "type": "PLA", "color": "#000000", "spoolman_spool_id": 42}],
    )
    job_id = await create_job(file_id, printer_id)
    # Spoolman left at its default (disabled).

    mock_qe = MagicMock()
    mock_qe._slicer._data_dir = tmp_path

    async def fake_run_verify_slice(req, output_dir):
        return _fake_gcode_with_estimates(output_dir, grams=8.0)

    mock_qe.run_verify_slice = fake_run_verify_slice

    with patch("app.api.routes.jobs.queue_engine", mock_qe), \
         patch("app.api.routes.jobs._deduct_spool") as mock_deduct:
        resp = await client.post(
            f"/api/v1/jobs/{job_id}/complete-manually", json={"printer_id": printer_id},
        )

    assert resp.status_code == 200
    mock_deduct.assert_not_called()
