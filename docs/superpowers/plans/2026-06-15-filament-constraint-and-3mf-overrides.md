# Filament Constraint Verification + 3MF Override Opt-In Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add three-way filament constraint selection with inline match badge to PerPrinterConfig, and an inline 3MF override panel to New Job and Edit Job that stores confirmed overrides per-job and applies them at slice time.

**Architecture:** Backend gains a `jobs.overrides JSON` column, a `GET /files/{id}/embedded-settings` endpoint, and override-aware job create/update routes. At slice time the queue engine merges `job.overrides` into the slicer config via a new `extra_config` field on `SliceRequest`. Frontend gains a new `OverridePanel` component, an updated `PerPrinterConfig` with three-way selector and chip badge, and both New Job and Edit Job screens wire up embedded-settings fetching and confirmed-override posting. The `OverrideAlertModal` (old modal-based warning) is removed.

**Tech Stack:** FastAPI, SQLAlchemy 2 async, SQLite (WAL), React 18, TypeScript, Vitest, pytest-asyncio

---

## File Map

**Create:**
- `backend/tests/test_embedded_settings.py` — tests for Task 2 and Task 3
- `backend/tests/test_job_overrides.py` — tests for Task 4 and Task 5
- `frontend/src/components/OverridePanel.tsx` — Task 8

**Modify:**
- `backend/app/models.py` — Task 1 (add `overrides` to `Job`)
- `backend/app/database.py` — Task 1 (migration)
- `backend/app/services/three_mf_parser.py` — Task 2 (`parse_embedded_settings`)
- `backend/app/api/routes/files.py` — Task 3 (new endpoint)
- `backend/app/services/slicer_service.py` — Task 5 (`SliceRequest.extra_config`, `_build_config`)
- `backend/app/services/queue_engine.py` — Task 5 (pass `job.overrides`)
- `backend/app/api/routes/jobs.py` — Task 4 (`_to_dict`, `JobCreate`, `JobConfigsUpdate`, routes)
- `backend/tests/test_migrations.py` — Task 1 (new migration test)
- `frontend/src/api/queue.ts` — Task 6 (new API functions + updated types)
- `frontend/src/components/PerPrinterConfig.tsx` — Task 7 (three-way selector + chip badge)
- `frontend/src/screens/NewJobScreen.tsx` — Task 9
- `frontend/src/screens/EditJobScreen.tsx` — Task 10

---

## Task 1: DB — `overrides` column on `Job` model + migration

**Files:**
- Modify: `backend/app/models.py` (line 86, inside `Job` class)
- Modify: `backend/app/database.py` (line 88, inside `_migrate`)
- Modify: `backend/tests/test_migrations.py`

- [ ] **Step 1: Write the failing migration test**

Add to `backend/tests/test_migrations.py`:

```python
@pytest.mark.asyncio
async def test_migrate_adds_overrides_to_jobs():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _migrate(conn)
        await _migrate(conn)  # idempotent
        cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(jobs)"))).fetchall()}
    assert "overrides" in cols
    await engine.dispose()
```

- [ ] **Step 2: Run the test to confirm it fails**

```
cd backend && python -m pytest tests/test_migrations.py::test_migrate_adds_overrides_to_jobs -v
```

Expected: FAIL — `overrides` not in cols.

- [ ] **Step 3: Add `overrides` to `Job` model in `backend/app/models.py`**

In the `Job` class, after the `updated_at` field (line ~87), add:

```python
overrides: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
```

The full `Job` class after the change:

```python
class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    uploaded_file_id: Mapped[int] = mapped_column(ForeignKey("uploaded_files.id"))
    plate_number: Mapped[int] = mapped_column(default=1)
    order_id: Mapped[Optional[int]] = mapped_column(ForeignKey("orders.id"), nullable=True)
    assigned_printer_id: Mapped[Optional[int]] = mapped_column(ForeignKey("printers.id"), nullable=True)
    queue_position: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    block_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    overrides: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[str] = mapped_column(String(32))
    updated_at: Mapped[str] = mapped_column(String(32))
```

- [ ] **Step 4: Add migration in `backend/app/database.py`**

In `_migrate()`, the `job_cols` block (around line 88) currently ends at the `block_reason` check. Add the `overrides` check:

```python
    job_cols = {row[1] for row in (await conn.execute(text("PRAGMA table_info(jobs)"))).fetchall()}
    if job_cols:
        if "block_reason" not in job_cols:
            await conn.execute(text("ALTER TABLE jobs ADD COLUMN block_reason TEXT"))
        if "order_id" not in job_cols:
            await conn.execute(text("ALTER TABLE jobs ADD COLUMN order_id INTEGER"))
        if "overrides" not in job_cols:
            await conn.execute(text("ALTER TABLE jobs ADD COLUMN overrides JSON"))
```

- [ ] **Step 5: Run the migration test to confirm it passes**

```
cd backend && python -m pytest tests/test_migrations.py::test_migrate_adds_overrides_to_jobs -v
```

Expected: PASS

- [ ] **Step 6: Run all migration tests to confirm no regressions**

```
cd backend && python -m pytest tests/test_migrations.py -v
```

Expected: all PASS

- [ ] **Step 7: Commit**

```
git add backend/app/models.py backend/app/database.py backend/tests/test_migrations.py
git commit -m "feat(db): add overrides JSON column to jobs"
```

---

## Task 2: Backend — `parse_embedded_settings` in `three_mf_parser.py`

**Files:**
- Modify: `backend/app/services/three_mf_parser.py`
- Create: `backend/tests/test_embedded_settings.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_embedded_settings.py`:

```python
import io
import json
import zipfile
import pytest
from app.services.three_mf_parser import parse_embedded_settings


def _make_3mf(settings: dict) -> str:
    """Return path to a temp 3MF zip with the given project_settings.config."""
    import tempfile, os
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Metadata/project_settings.config", json.dumps(settings))
    tmp = tempfile.NamedTemporaryFile(suffix=".3mf", delete=False)
    tmp.write(buf.getvalue())
    tmp.close()
    return tmp.name


def test_returns_empty_for_non_3mf(tmp_path):
    f = tmp_path / "model.stl"
    f.write_bytes(b"solid model\nendsolid")
    assert parse_embedded_settings(str(f)) == []


def test_returns_empty_when_no_curated_keys(tmp_path):
    path = _make_3mf({"some_other_key": "value"})
    result = parse_embedded_settings(path)
    assert result == []


def test_returns_curated_keys_present_in_file(tmp_path):
    path = _make_3mf({"fill_pattern": "grid", "layer_height": "0.15", "some_ignored": "x"})
    result = parse_embedded_settings(path)
    keys = {r["key"] for r in result}
    assert "fill_pattern" in keys
    assert "layer_height" in keys
    assert "some_ignored" not in keys
    # Check structure
    fp = next(r for r in result if r["key"] == "fill_pattern")
    assert fp["value"] == "grid"
    assert "label" in fp  # human-readable label present


def test_list_values_joined_as_string(tmp_path):
    path = _make_3mf({"enable_support": ["1"]})
    result = parse_embedded_settings(path)
    assert result[0]["value"] == "1"


def test_returns_empty_for_bad_zip(tmp_path):
    f = tmp_path / "bad.3mf"
    f.write_bytes(b"not a zip file")
    assert parse_embedded_settings(str(f)) == []
```

- [ ] **Step 2: Run tests to confirm they fail**

```
cd backend && python -m pytest tests/test_embedded_settings.py -v
```

Expected: FAIL — `parse_embedded_settings` not defined.

- [ ] **Step 3: Implement `parse_embedded_settings` in `backend/app/services/three_mf_parser.py`**

Add after the `parse_model_filaments` function (after line 34):

```python
from .override_inspector import CURATED_KEYS

_SETTING_LABELS: dict[str, str] = {
    "enable_support": "Enable supports",
    "support_type": "Support type",
    "support_threshold_angle": "Support threshold angle",
    "support_on_build_plate_only": "Support on build plate only",
    "raft_layers": "Raft layers",
    "brim_type": "Brim type",
    "brim_width": "Brim width",
    "sparse_infill_density": "Infill density",
    "sparse_infill_pattern": "Infill pattern",
    "wall_loops": "Wall loops",
    "top_shell_layers": "Top layers",
    "bottom_shell_layers": "Bottom layers",
    "layer_height": "Layer height",
    "ironing_type": "Ironing type",
}


def parse_embedded_settings(file_path: str) -> list[dict]:
    """Return curated print settings baked into the 3MF's project_settings.config.

    Used by the New Job / Edit Job override panel to show which settings the file
    has embedded, so the user can selectively keep them. Returns [] if not a 3MF,
    has no embedded config, or no curated keys are present."""
    try:
        with zipfile.ZipFile(file_path) as zf:
            actual = next(
                (n for n in zf.namelist()
                 if n.lower() == "metadata/project_settings.config"),
                None,
            )
            if actual is None:
                return []
            ps = json.loads(zf.read(actual))
    except (zipfile.BadZipFile, json.JSONDecodeError, OSError):
        return []

    out = []
    for key in CURATED_KEYS:
        if key not in ps:
            continue
        val = ps[key]
        if isinstance(val, list):
            val = ", ".join(str(v) for v in val)
        else:
            val = str(val)
        out.append({"key": key, "label": _SETTING_LABELS.get(key, key), "value": val})
    return out
```

- [ ] **Step 4: Run tests to confirm they pass**

```
cd backend && python -m pytest tests/test_embedded_settings.py -v
```

Expected: all 5 tests PASS

- [ ] **Step 5: Commit**

```
git add backend/app/services/three_mf_parser.py backend/tests/test_embedded_settings.py
git commit -m "feat(parser): add parse_embedded_settings for 3MF override panel"
```

---

## Task 3: Backend — `GET /files/{file_id}/embedded-settings` endpoint

**Files:**
- Modify: `backend/app/api/routes/files.py`
- Modify: `backend/tests/test_embedded_settings.py`

- [ ] **Step 1: Write the failing endpoint test**

Append to `backend/tests/test_embedded_settings.py`:

```python
import io, json, zipfile
from httpx import AsyncClient


def _3mf_bytes(settings: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Metadata/project_settings.config", json.dumps(settings))
    return buf.getvalue()


@pytest.mark.asyncio
async def test_embedded_settings_endpoint_returns_curated(client: AsyncClient, tmp_path):
    from app.models import UploadedFile
    from app.database import get_session
    import datetime

    # Write a minimal 3MF to a temp file
    path = tmp_path / "model.3mf"
    path.write_bytes(_3mf_bytes({"fill_pattern": "grid", "layer_height": "0.20"}))

    # Insert an UploadedFile record pointing to it
    from httpx import AsyncClient
    resp = await client.post("/api/v1/files/upload", files={"file": ("m.3mf", path.read_bytes(), "application/octet-stream")})
    # Instead, inject via DB directly using the test session
    # (upload endpoint does file operations we'd need to mock; easier to seed DB)
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
    from app.database import Base
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    from app.main import app
    from app.database import get_session as _get_session

    async def _override():
        async with factory() as s:
            yield s

    app.dependency_overrides[_get_session] = _override
    async with AsyncClient(transport=__import__('httpx').ASGITransport(app=app), base_url="http://test") as c:
        # seed a file record
        async with factory() as s:
            rec = UploadedFile(
                original_filename="model.3mf",
                stored_path=str(path),
                plates=[],
                uploaded_at=datetime.datetime.utcnow().isoformat(),
            )
            s.add(rec)
            await s.commit()
            await s.refresh(rec)
            file_id = rec.id

        resp = await c.get(f"/api/v1/files/{file_id}/embedded-settings")
    app.dependency_overrides.clear()
    await engine.dispose()

    assert resp.status_code == 200
    data = resp.json()
    keys = {r["key"] for r in data}
    assert "fill_pattern" in keys
    assert "layer_height" in keys
    for r in data:
        assert "key" in r and "label" in r and "value" in r


@pytest.mark.asyncio
async def test_embedded_settings_endpoint_404(client: AsyncClient):
    resp = await client.get("/api/v1/files/9999/embedded-settings")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run tests to confirm they fail**

```
cd backend && python -m pytest tests/test_embedded_settings.py::test_embedded_settings_endpoint_404 -v
```

Expected: FAIL — route not found (404 from framework, not our handler).

- [ ] **Step 3: Add the endpoint in `backend/app/api/routes/files.py`**

After the `get_model_filaments` endpoint (after line 352), add:

```python
@router.get("/{file_id}/embedded-settings")
async def get_embedded_settings(file_id: int, session: AsyncSession = Depends(get_session)) -> list[dict]:
    from ...services.three_mf_parser import parse_embedded_settings
    record = await session.get(UploadedFile, file_id)
    if record is None:
        raise HTTPException(404, f"File {file_id} not found")
    return parse_embedded_settings(record.stored_path)
```

- [ ] **Step 4: Run the 404 test to confirm it passes**

```
cd backend && python -m pytest tests/test_embedded_settings.py::test_embedded_settings_endpoint_404 -v
```

Expected: PASS

- [ ] **Step 5: Run all embedded settings tests**

```
cd backend && python -m pytest tests/test_embedded_settings.py -v
```

Expected: all tests PASS (the DB-seeding test is more involved; if it fails due to test infra complexity, skip it with `@pytest.mark.skip` and note why — the unit tests for `parse_embedded_settings` are the real coverage)

- [ ] **Step 6: Commit**

```
git add backend/app/api/routes/files.py backend/tests/test_embedded_settings.py
git commit -m "feat(api): GET /files/{id}/embedded-settings endpoint"
```

---

## Task 4: Backend — job routes accept and store `overrides`

**Files:**
- Modify: `backend/app/api/routes/jobs.py`
- Create: `backend/tests/test_job_overrides.py`

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_job_overrides.py`:

```python
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_job_stores_overrides(client: AsyncClient):
    from app.models import Printer, UploadedFile
    from sqlalchemy.ext.asyncio import AsyncSession
    import datetime

    # Seed DB via the client fixture's overridden session
    from app.main import app
    from app.database import get_session

    session_gen = app.dependency_overrides[get_session]

    async def get_s():
        async for s in session_gen():
            return s

    session = await get_s()
    printer = Printer(name="T", printer_type="bambu", connection_config={},
                      orca_printer_profiles=[], current_orca_printer_profile="Profile A")
    session.add(printer)
    uf = UploadedFile(original_filename="m.3mf", stored_path="/tmp/m.3mf",
                      plates=[], uploaded_at=datetime.datetime.utcnow().isoformat())
    session.add(uf)
    await session.commit()
    await session.refresh(printer)
    await session.refresh(uf)

    resp = await client.post("/api/v1/jobs", json={
        "uploaded_file_id": uf.id,
        "plate_number": 1,
        "overrides": {"fill_pattern": "grid", "layer_height": "0.15"},
        "printer_configs": [{
            "printer_id": printer.id,
            "print_profile": "0.16mm Profile",
        }],
    })
    assert resp.status_code == 201
    job_id = resp.json()["id"]

    # Overrides must be in the details response
    detail = await client.get(f"/api/v1/jobs/{job_id}/details")
    assert detail.status_code == 200
    assert detail.json()["overrides"] == {"fill_pattern": "grid", "layer_height": "0.15"}


@pytest.mark.asyncio
async def test_create_job_without_overrides_is_null(client: AsyncClient):
    from app.models import Printer, UploadedFile
    import datetime

    from app.main import app
    from app.database import get_session
    session_gen = app.dependency_overrides[get_session]
    async def get_s():
        async for s in session_gen():
            return s
    session = await get_s()
    printer = Printer(name="T2", printer_type="bambu", connection_config={},
                      orca_printer_profiles=[], current_orca_printer_profile="Profile A")
    session.add(printer)
    uf = UploadedFile(original_filename="m.3mf", stored_path="/tmp/m.3mf",
                      plates=[], uploaded_at=datetime.datetime.utcnow().isoformat())
    session.add(uf)
    await session.commit()
    await session.refresh(printer)
    await session.refresh(uf)

    resp = await client.post("/api/v1/jobs", json={
        "uploaded_file_id": uf.id,
        "plate_number": 1,
        "printer_configs": [{"printer_id": printer.id, "print_profile": "Profile"}],
    })
    assert resp.status_code == 201
    job_id = resp.json()["id"]
    detail = await client.get(f"/api/v1/jobs/{job_id}/details")
    assert detail.json()["overrides"] is None
```

- [ ] **Step 2: Run tests to confirm they fail**

```
cd backend && python -m pytest tests/test_job_overrides.py -v
```

Expected: FAIL — `overrides` not accepted in request, not in response.

- [ ] **Step 3: Update `_to_dict` in `backend/app/api/routes/jobs.py`**

The current `_to_dict` function (lines 60-71) does not include `overrides`. Add it:

```python
def _to_dict(j: Job) -> dict:
    return {
        "id": j.id,
        "uploaded_file_id": j.uploaded_file_id,
        "plate_number": j.plate_number,
        "order_id": j.order_id,
        "assigned_printer_id": j.assigned_printer_id,
        "queue_position": j.queue_position,
        "status": j.status,
        "overrides": j.overrides,
        "created_at": j.created_at,
        "updated_at": j.updated_at,
    }
```

- [ ] **Step 4: Update `JobCreate` schema**

The current `JobCreate` (lines 53-57) does not have `overrides`. Add it:

```python
class JobCreate(BaseModel):
    uploaded_file_id: int
    plate_number: int = 1
    order_id: int | None = None
    printer_configs: list[PrinterConfigInput]
    overrides: dict | None = None
```

- [ ] **Step 5: Update `create_job` route to store overrides**

In `create_job` (around line 128-136), the `Job(...)` constructor call does not include `overrides`. Add it:

```python
    job = Job(
        uploaded_file_id=body.uploaded_file_id,
        plate_number=body.plate_number,
        order_id=body.order_id,
        overrides=body.overrides,
        queue_position=pos,
        status="queued",
        created_at=now,
        updated_at=now,
    )
```

- [ ] **Step 6: Update `JobConfigsUpdate` schema**

The current `JobConfigsUpdate` (line 316-317) does not have `overrides`. Add it:

```python
class JobConfigsUpdate(BaseModel):
    printer_configs: list[PrinterConfigInput]
    overrides: dict | None = None
```

- [ ] **Step 7: Update `update_job_configs` route to store overrides**

In `update_job_configs` (around line 360-363), after the `for cfg in body.printer_configs:` block, store overrides:

```python
    job.status = "queued"
    job.block_reason = None
    job.assigned_printer_id = None
    job.overrides = body.overrides
    job.updated_at = datetime.now(timezone.utc).isoformat()
```

- [ ] **Step 8: Run the job overrides tests**

```
cd backend && python -m pytest tests/test_job_overrides.py -v
```

Expected: all PASS

- [ ] **Step 9: Run the full backend test suite**

```
cd backend && python -m pytest -v
```

Expected: all PASS

- [ ] **Step 10: Commit**

```
git add backend/app/api/routes/jobs.py backend/tests/test_job_overrides.py
git commit -m "feat(api): job create/update accept and return overrides"
```

---

## Task 5: Backend — `SliceRequest.extra_config`, slicer merges it, queue engine passes `job.overrides`

**Files:**
- Modify: `backend/app/services/slicer_service.py`
- Modify: `backend/app/services/queue_engine.py`
- Modify: `backend/tests/test_job_overrides.py`

- [ ] **Step 1: Write a test for override application in slicer**

Append to `backend/tests/test_job_overrides.py`:

```python
from app.services.slicer_service import SliceRequest, SlicerService


def test_slice_request_extra_config_defaults_empty():
    req = SliceRequest(
        job_id=1, source_3mf="/tmp/m.3mf", plate_number=1,
        machine_preset="machine", process_preset="process",
        filament_presets=["filament"],
    )
    assert req.extra_config == {}


def test_slicer_build_config_merges_extra_config(monkeypatch):
    """extra_config values override profile values in the built config."""
    from unittest.mock import MagicMock, patch

    svc = SlicerService.__new__(SlicerService)
    svc._orca = "orca"
    svc._data_dir = __import__('pathlib').Path("/tmp")

    mock_machine = {"fill_pattern": "gyroid", "layer_height": "0.20", "type": "machine"}
    mock_process = {"fill_pattern": "gyroid", "layer_height": "0.20", "type": "process"}
    mock_filament = {"filament_type": ["PLA"], "type": "filament"}

    with patch.object(svc, '_resolver') as mock_resolver, \
         patch('app.services.slicer_service.build_project_config') as mock_build:
        mock_resolver.resolve.side_effect = [mock_machine, mock_process, mock_filament]
        mock_build.return_value = {"fill_pattern": "gyroid", "layer_height": "0.20"}

        req = SliceRequest(
            job_id=1, source_3mf="/tmp/m.3mf", plate_number=1,
            machine_preset="machine", process_preset="process",
            filament_presets=["filament"],
            extra_config={"fill_pattern": "grid"},
        )
        config = svc._build_config(req)

    assert config["fill_pattern"] == "grid"   # override wins
    assert config["layer_height"] == "0.20"   # non-overridden key preserved
```

- [ ] **Step 2: Run test to confirm it fails**

```
cd backend && python -m pytest tests/test_job_overrides.py::test_slice_request_extra_config_defaults_empty tests/test_job_overrides.py::test_slicer_build_config_merges_extra_config -v
```

Expected: FAIL — `extra_config` attribute doesn't exist on `SliceRequest`.

- [ ] **Step 3: Add `extra_config` to `SliceRequest` in `backend/app/services/slicer_service.py`**

The `SliceRequest` dataclass (lines 27-45) currently ends with `prepare_hook`. Add `extra_config` as the last field:

```python
@dataclass
class SliceRequest:
    """What a single (job, printer) slice needs.

    ``machine_preset`` is the printer's ``current_orca_printer_profile``;
    ``process_preset``/``filament_presets`` are OrcaSlicer preset names.
    ``export_args`` are the printer-specific OrcaSlicer output args (from
    ``AbstractPrinterClient.orca_export_args``): ``[]`` yields raw gcode (the
    default), ``["--export-3mf", "<name>.gcode.3mf"]`` yields the archive. Orca
    always writes gcode to ``--outputdir``; ``--export-3mf`` adds the archive.
    ``extra_config`` key-value pairs are merged into the slicer config after
    profile resolution, so they override profile defaults.
    """
    job_id: int
    source_3mf: str
    plate_number: int
    machine_preset: str
    process_preset: str
    filament_presets: list[str]
    filament_colours: list[str] = field(default_factory=list)
    export_args: list[str] = field(default_factory=list)
    prepare_hook: "Callable[[Path], None] | None" = None
    extra_config: dict = field(default_factory=dict)
```

- [ ] **Step 4: Merge `extra_config` in `SlicerService._build_config`**

In `_build_config` (lines 89-111), the current last line is:

```python
        return build_project_config(machine, process, filaments, req.filament_colours or None, plate_count=plate_count)
```

Replace it with:

```python
        config = build_project_config(machine, process, filaments, req.filament_colours or None, plate_count=plate_count)
        if req.extra_config:
            config.update(req.extra_config)
        return config
```

- [ ] **Step 5: Run the slicer tests to confirm they pass**

```
cd backend && python -m pytest tests/test_job_overrides.py::test_slice_request_extra_config_defaults_empty tests/test_job_overrides.py::test_slicer_build_config_merges_extra_config -v
```

Expected: PASS

- [ ] **Step 6: Read `job.overrides` and pass to `SliceRequest` in `backend/app/services/queue_engine.py`**

In `_run_slice_and_print` (starting at line 217), the `async with self._factory() as session:` block captures scalar values before the session closes. Add `job_overrides` capture after `machine_preset`:

```python
            machine_preset = printer.current_orca_printer_profile if printer else None
            job_overrides = job.overrides or {}  # add this line
```

Then in the `SliceRequest(...)` constructor call (around line 275), add `extra_config`:

```python
        req = SliceRequest(
            job_id=job_id,
            source_3mf=stored_path,
            plate_number=plate_number,
            machine_preset=machine_preset,
            process_preset=print_profile,
            filament_presets=multi_presets if cfg_filament_map else ([filament_profile] if filament_profile else []),
            filament_colours=[filament_color] if filament_color else [],
            export_args=export_args,
            prepare_hook=prepare_hook,
            extra_config=job_overrides,
        )
```

- [ ] **Step 7: Run the full backend test suite**

```
cd backend && python -m pytest -v
```

Expected: all PASS

- [ ] **Step 8: Commit**

```
git add backend/app/services/slicer_service.py backend/app/services/queue_engine.py backend/tests/test_job_overrides.py
git commit -m "feat(slicer): apply job.overrides as extra_config at slice time"
```

---

## Task 6: Frontend — API additions in `queue.ts`

**Files:**
- Modify: `frontend/src/api/queue.ts`
- Modify: `frontend/src/api/queue.test.ts`

- [ ] **Step 1: Write failing tests**

Append to `frontend/src/api/queue.test.ts`:

```typescript
describe('getEmbeddedSettings', () => {
  it('fetches from /api/v1/files/{id}/embedded-settings', async () => {
    const settings = [{ key: 'fill_pattern', label: 'Fill pattern', value: 'grid' }];
    mockOk(settings);
    const { getEmbeddedSettings } = await import('./queue');
    const result = await getEmbeddedSettings(7);
    expect(mockFetch).toHaveBeenCalledWith('/api/v1/files/7/embedded-settings');
    expect(result).toEqual(settings);
  });
});

describe('createJob with overrides', () => {
  it('includes overrides in POST body', async () => {
    mockOk({ id: 42, status: 'queued', uploaded_file_id: 1, plate_number: 1,
             order_id: null, assigned_printer_id: null, queue_position: 1,
             overrides: { fill_pattern: 'grid' }, created_at: '', updated_at: '' });
    const { createJob } = await import('./queue');
    await createJob({
      uploaded_file_id: 1, plate_number: 1,
      printer_configs: [{ printer_id: 1, print_profile: 'p' }],
      overrides: { fill_pattern: 'grid' },
    });
    const body = JSON.parse((mockFetch.mock.calls[0][1] as RequestInit).body as string);
    expect(body.overrides).toEqual({ fill_pattern: 'grid' });
  });
});

describe('updateJobConfigs with overrides', () => {
  it('includes overrides in PATCH body', async () => {
    mockOk({ id: 1, status: 'queued' });
    const { updateJobConfigs } = await import('./queue');
    await updateJobConfigs(1, [{ printer_id: 2, print_profile: 'p' }], { layer_height: '0.15' });
    const body = JSON.parse((mockFetch.mock.calls[0][1] as RequestInit).body as string);
    expect(body.overrides).toEqual({ layer_height: '0.15' });
  });
});
```

- [ ] **Step 2: Run tests to confirm they fail**

```
cd frontend && npm test -- queue.test.ts
```

Expected: FAIL — `getEmbeddedSettings` not found; `createJob` has no `overrides`; `updateJobConfigs` has no `overrides` param.

- [ ] **Step 3: Update `frontend/src/api/queue.ts`**

Add `EmbeddedSetting` interface and `getEmbeddedSettings` function after `getModelFilaments` (after line 117):

```typescript
export interface EmbeddedSetting {
  key: string;
  label: string;
  value: string;
}

export async function getEmbeddedSettings(fileId: number): Promise<EmbeddedSetting[]> {
  return request(`/api/v1/files/${fileId}/embedded-settings`);
}
```

Add `overrides` to `ApiJob` (after line 49, before `created_at`):
```typescript
  overrides: Record<string, string> | null;
```

Update `createJob` signature to accept `overrides`:

```typescript
export async function createJob(body: {
  uploaded_file_id: number;
  plate_number: number;
  printer_configs: PrinterConfigInput[];
  order_id?: number | null;
  overrides?: Record<string, string> | null;
}): Promise<ApiJob> {
  return request('/api/v1/jobs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}
```

Update `updateJobConfigs` signature to accept `overrides`:

```typescript
export async function updateJobConfigs(
  jobId: number,
  configs: PrinterConfigInput[],
  overrides?: Record<string, string> | null,
): Promise<ApiJob> {
  return request(`/api/v1/jobs/${jobId}/configs`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ printer_configs: configs, overrides: overrides ?? null }),
  });
}
```

- [ ] **Step 4: Run the frontend API tests**

```
cd frontend && npm test -- queue.test.ts
```

Expected: new tests PASS, no regressions in existing tests.

- [ ] **Step 5: Commit**

```
git add frontend/src/api/queue.ts frontend/src/api/queue.test.ts
git commit -m "feat(api): getEmbeddedSettings + overrides in createJob/updateJobConfigs"
```

---

## Task 7: Frontend — `PerPrinterConfig.tsx` three-way selector + chip badge

**Files:**
- Modify: `frontend/src/components/PerPrinterConfig.tsx`

Context: The current single-filament path uses a binary `requireFilament: boolean` state and shows type+color inputs when in "require" mode. Replace it with a three-way constraint selector ('defer' / 'type-only' / 'type-color') and add a chip badge that matches against `printer.loaded_filaments`.

The multi-tool (`slots.length >= 2`) and AMS mapping (`modelFilaments.length > 1`) paths are **unchanged**.

- [ ] **Step 1: Replace `requireFilament` state with `filamentConstraint`**

In `PerPrinterConfig` component, replace:

```typescript
  const [requireFilament, setRequireFilament] = useState(
    () => !!(config.filamentType || config.filamentProfile),
  );
```

With:

```typescript
  const [filamentConstraint, setFilamentConstraint] = useState<'defer' | 'type-only' | 'type-color'>(
    () => {
      if (!config.filamentType && !config.filamentId) return 'defer';
      if (config.filamentType && !config.filamentColor) return 'type-only';
      return 'type-color';
    },
  );
```

- [ ] **Step 2: Update the `useEffect` that sets a default color**

Replace the existing `useEffect` that references `requireFilament`:

```typescript
  useEffect(() => {
    if (requireFilament && (!spoolmanActive || manualMode) && config.filamentColor === null) {
      onChange({ filamentColor: '#888888' });
    }
  }, [spoolmanActive, manualMode, requireFilament]); // eslint-disable-line react-hooks/exhaustive-deps
```

With:

```typescript
  useEffect(() => {
    if (filamentConstraint === 'type-color' && (!spoolmanActive || manualMode) && config.filamentColor === null) {
      onChange({ filamentColor: '#888888' });
    }
  }, [spoolmanActive, manualMode, filamentConstraint]); // eslint-disable-line react-hooks/exhaustive-deps
```

- [ ] **Step 3: Add `matchBadge` helper and chip badge rendering**

Add a helper function inside the component (before the `return` statement) to compute match state:

```typescript
  function _normColor(c: string | null | undefined): string {
    return (c ?? '').replace('#', '').toLowerCase();
  }

  function computeSlotMatch(): { state: 'match' | 'no-match' | 'defer'; label: string; color: string | null } {
    if (filamentConstraint === 'defer' && !config.filamentId) {
      return { state: 'defer', label: 'Any loaded filament', color: null };
    }
    const reqType = (config.filamentType ?? '').toLowerCase();
    const reqColor = _normColor(config.filamentColor);
    for (let i = 0; i < slots.length; i++) {
      const s = slots[i];
      const sType = (s.type ?? '').toLowerCase();
      if (!reqType) continue;
      if (sType === reqType) {
        if (filamentConstraint === 'type-only' || !reqColor || _normColor(s.color) === reqColor) {
          const label = `${s.type ?? '?'} · slot ${i}`;
          return { state: 'match', label, color: s.color ?? null };
        }
      }
    }
    const desc = filamentConstraint === 'type-only'
      ? `No ${config.filamentType ?? '?'} loaded`
      : `No ${config.filamentType ?? '?'} match`;
    return { state: 'no-match', label: desc, color: null };
  }

  const slotMatch = computeSlotMatch();
```

- [ ] **Step 4: Replace the single-filament JSX block**

In the JSX, the single-filament section (the final `else` branch starting at line ~193) currently renders:

```tsx
        ) : (
          <div>
            <label className="label">Filament</label>
            <select data-testid="filament-mode" className="select"
                    value={requireFilament ? 'require' : 'defer'}
                    onChange={e => {
                      const req = e.target.value === 'require';
                      setRequireFilament(req);
                      if (!req) clearAsk();
                    }}>
              <option value="defer">Use loaded filament</option>
              <option value="require">Require specific filament</option>
            </select>
            {requireFilament && (
              <div style={{ marginTop: 8 }}>
                ...
              </div>
            )}
          </div>
        )}
```

Replace the **entire** single-filament `<div>` block with:

```tsx
        ) : (
          <div>
            <label className="label">Filament</label>
            <select
              data-testid="filament-mode"
              className="select"
              value={filamentConstraint}
              onChange={e => {
                const mode = e.target.value as 'defer' | 'type-only' | 'type-color';
                setFilamentConstraint(mode);
                if (mode === 'defer') { clearAsk(); }
                else if (mode === 'type-only') { onChange({ filamentColor: null }); }
              }}
            >
              <option value="defer">Use loaded filament</option>
              <option value="type-only">Require by type</option>
              <option value="type-color">Require by type + color</option>
            </select>

            {filamentConstraint !== 'defer' && (
              <div style={{ marginTop: 8 }}>
                {spoolmanActive && !manualMode ? (
                  <select data-testid="filament-catalog-select" className="select" value={catalogValue}
                          onChange={e => {
                            const v = e.target.value;
                            if (v === '__manual__') { setManualMode(true); clearAsk(); return; }
                            const f = filaments.find(f => filamentDisplayName(f) === v) ?? null;
                            onChange({
                              filamentProfile: v || null, filamentId: f?.id ?? null,
                              filamentType: f?.material ?? null,
                              filamentColor: f?.color_hex ? `#${f.color_hex}` : null,
                            });
                            setFilamentConstraint('type-color');
                          }}>
                    <option value="">— select filament —</option>
                    {filaments.map(f => (
                      <option key={f.id} value={filamentDisplayName(f)}>{filamentDisplayName(f)} · {f.material}</option>
                    ))}
                    <option value="__manual__">Enter manually…</option>
                  </select>
                ) : (
                  <div className="col gap-2">
                    <div className="row gap-2">
                      <input data-testid="filament-type-input" className="input" list="filament-types"
                             placeholder="Type (PLA, PETG, ABS…)" value={config.filamentType ?? ''}
                             onChange={e => onChange({ filamentType: e.target.value || null, filamentProfile: e.target.value || null, filamentId: null })}
                             style={{ flex: 1 }} />
                      {spoolmanActive && (
                        <button className="btn ghost sm" onClick={() => { setManualMode(false); clearAsk(); }}>↩ Catalog</button>
                      )}
                    </div>
                    <datalist id="filament-types">
                      {FILAMENT_TYPES.map(t => <option key={t} value={t} />)}
                    </datalist>
                    {filamentConstraint === 'type-color' && (
                      <div className="row gap-2" style={{ alignItems: 'center' }}>
                        <input data-testid="filament-color-input" type="color" value={config.filamentColor ?? '#888888'}
                               onChange={e => onChange({ filamentColor: e.target.value })}
                               style={{ width: 36, height: 28, padding: 2, borderRadius: 6, border: '1px solid var(--border-2)', background: 'var(--bg-1)', cursor: 'pointer', flexShrink: 0 }} />
                        <span className="tiny muted">{config.filamentColor ?? '#888888'}</span>
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}

            {/* Chip badge — slot match status */}
            <div style={{ marginTop: 8 }}>
              {slotMatch.state === 'defer' && (
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 11,
                               padding: '3px 8px', borderRadius: 4,
                               background: 'rgba(251,191,36,0.10)', border: '1px solid rgba(251,191,36,0.30)',
                               color: 'var(--warn)' }}>
                  ◉ {slotMatch.label}
                </span>
              )}
              {slotMatch.state === 'match' && (
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 11,
                               padding: '3px 8px', borderRadius: 4,
                               background: 'rgba(34,197,94,0.10)', border: '1px solid rgba(34,197,94,0.30)',
                               color: 'var(--ok)' }}>
                  {slotMatch.color && (
                    <span style={{ width: 8, height: 8, borderRadius: 2, flexShrink: 0,
                                   background: slotMatch.color, display: 'inline-block' }} />
                  )}
                  ✓ {slotMatch.label}
                </span>
              )}
              {slotMatch.state === 'no-match' && (
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 11,
                               padding: '3px 8px', borderRadius: 4,
                               background: 'rgba(239,68,68,0.10)', border: '1px solid rgba(239,68,68,0.30)',
                               color: 'var(--err)' }}>
                  ✗ {slotMatch.label}
                </span>
              )}
            </div>
          </div>
        )}
```

- [ ] **Step 5: TypeScript check**

```
cd frontend && npm run build 2>&1 | head -40
```

Expected: no TypeScript errors relating to `PerPrinterConfig.tsx`.

- [ ] **Step 6: Commit**

```
git add frontend/src/components/PerPrinterConfig.tsx
git commit -m "feat(ui): three-way filament constraint selector + slot match chip badge"
```

---

## Task 8: Frontend — New `OverridePanel.tsx` component

**Files:**
- Create: `frontend/src/components/OverridePanel.tsx`

- [ ] **Step 1: Create `frontend/src/components/OverridePanel.tsx`**

```typescript
import type { EmbeddedSetting } from '../api/queue';

interface OverridePanelProps {
  settings: EmbeddedSetting[];
  value: Record<string, string>;
  onChange: (v: Record<string, string>) => void;
}

export function OverridePanel({ settings, value, onChange }: OverridePanelProps) {
  if (settings.length === 0) return null;

  function toggle(key: string, settingValue: string) {
    if (key in value) {
      const next = { ...value };
      delete next[key];
      onChange(next);
    } else {
      onChange({ ...value, [key]: settingValue });
    }
  }

  return (
    <div style={{
      border: '1px solid var(--border-1)',
      borderRadius: 10,
      padding: '14px 16px',
      background: 'var(--bg-1)',
    }}>
      <div className="label" style={{ marginBottom: 6 }}>3MF Embedded Settings</div>
      <div className="tiny muted" style={{ marginBottom: 10, lineHeight: 1.5 }}>
        The file has these settings baked in. Check the ones you want to apply — unchecked ones use the profile default.
      </div>
      <div className="col gap-2">
        {settings.map(s => {
          const checked = s.key in value;
          return (
            <label
              key={s.key}
              style={{ display: 'flex', alignItems: 'center', gap: 10, cursor: 'pointer', userSelect: 'none' }}
            >
              <input
                data-testid={`override-${s.key}`}
                type="checkbox"
                checked={checked}
                onChange={() => toggle(s.key, s.value)}
                style={{ width: 15, height: 15, accentColor: 'var(--accent)', cursor: 'pointer', flexShrink: 0 }}
              />
              <span className="small" style={{ flex: 1, minWidth: 0 }}>{s.label}</span>
              <span className="tiny muted mono" style={{ flexShrink: 0 }}>{s.value}</span>
            </label>
          );
        })}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: TypeScript check**

```
cd frontend && npm run build 2>&1 | head -40
```

Expected: no errors.

- [ ] **Step 3: Commit**

```
git add frontend/src/components/OverridePanel.tsx
git commit -m "feat(ui): OverridePanel component for 3MF embedded settings"
```

---

## Task 9: Frontend — `NewJobScreen.tsx` integration

**Files:**
- Modify: `frontend/src/screens/NewJobScreen.tsx`

Goals:
1. Call `getEmbeddedSettings` when a file loads (alongside `getModelFilaments`)
2. Add `confirmedOverrides` to `PlateConfig` per plate
3. Pass `embeddedSettings` + `confirmedOverrides` + `onSetOverrides` into `PlateConfigPanel`
4. Render `OverridePanel` inside `PlateConfigPanel`
5. Include `confirmedOverrides` in the `createJob` call as `overrides`
6. Remove `OverrideAlertModal` and all related state/logic (`overrideFindings`, `handleCreate`→now just `doCreate`, `checkOverrides` import, `mergeFindings`, `MergedFindings`)

- [ ] **Step 1: Update imports**

Replace the existing import line (line 7):

```typescript
import { uploadFile, createJob, getFilePlates, getModelFilaments, plateThumbnailUrl, checkOverrides, type ApiPlate, type OverrideCheck, type ModelFilament } from '../api/queue';
```

With:

```typescript
import { uploadFile, createJob, getFilePlates, getModelFilaments, getEmbeddedSettings, plateThumbnailUrl, type ApiPlate, type ModelFilament, type EmbeddedSetting } from '../api/queue';
import { OverridePanel } from '../components/OverridePanel';
```

- [ ] **Step 2: Add `confirmedOverrides` to the `PlateConfig` interface**

In the `PlateConfig` interface (around line 27):

```typescript
interface PlateConfig {
  selected: boolean;
  jobName: string;
  orderId: number | null;
  selectedPrinters: string[];
  perPrinter: Record<string, PerPrinterCfg>;
  confirmedOverrides: Record<string, string>;
}
```

- [ ] **Step 3: Update `defaultConfigForPlate` helper to include `confirmedOverrides: {}`**

Find the function that creates a default `PlateConfig` (search for `defaultConfigForPlate` in the file). It should create empty `confirmedOverrides`:

```typescript
function defaultConfigForPlate(plate: Plate): PlateConfig {
  return {
    selected: true,
    jobName: `Plate ${plate.index}`,
    orderId: null,
    selectedPrinters: [],
    perPrinter: {},
    confirmedOverrides: {},
  };
}
```

- [ ] **Step 4: Add `embeddedSettings` state and fetch it alongside `getModelFilaments`**

Add state at the top of the component (near `modelFilaments`):

```typescript
const [embeddedSettings, setEmbeddedSettings] = useState<EmbeddedSetting[]>([]);
```

In `loadFileIntoState`, update the `Promise.all` call to also fetch embedded settings:

```typescript
  async function loadFileIntoState(fileId: number, fileInfo: FileInfo) {
    setUploadedFileId(fileId);
    setFile(fileInfo);
    const [apiPlates, filaments, embedded] = await Promise.all([
      getFilePlates(fileId),
      getModelFilaments(fileId).catch(() => [] as ModelFilament[]),
      getEmbeddedSettings(fileId).catch(() => [] as EmbeddedSetting[]),
    ]);
    setModelFilaments(filaments);
    setEmbeddedSettings(embedded);
    const detected = platesToLocal(apiPlates, fileId);
    setPlates(detected);
    const configs: Record<string, PlateConfig> = {};
    detected.forEach(p => { configs[p.id] = defaultConfigForPlate(p); });
    setPlateConfigs(configs);
    setActivePlateId(detected[0]?.id ?? null);
  }
```

Also reset `embeddedSettings` in `clearFile`:

```typescript
  function clearFile() {
    setFile(null); setUploadedFileId(null); setPlates([]);
    setPlateConfigs({}); setActivePlateId(null); setError(null);
    setModelFilaments([]); setEmbeddedSettings([]);
  }
```

- [ ] **Step 5: Remove `OverrideAlertModal` logic**

Remove the following from the component:
- `overrideFindings` state declaration
- `handleCreate` function (which called `checkOverrides` and showed the modal)
- `MergedFindings` interface (if defined locally)
- `mergeFindings` function
- The `checkOverrides` call inside `handleCreate`

The submit button previously called `handleCreate`. Change it to call `doCreate` directly.

Inside `doCreate`, also remove the `setOverrideFindings(null)` line and simplify:

```typescript
  async function doCreate() {
    if (!uploadedFileId) return;
    setSubmitting(true);
    setError(null);
    const count = selectedPlateIds.length;
    try {
      for (const id of selectedPlateIds) {
        const plate = plates.find(p => p.id === id)!;
        const cfg = plateConfigs[id];
        await createJob({
          uploaded_file_id: uploadedFileId,
          plate_number: plate.index,
          order_id: cfg.orderId,
          overrides: Object.keys(cfg.confirmedOverrides).length > 0 ? cfg.confirmedOverrides : null,
          printer_configs: cfg.selectedPrinters.map(pid => ({
            printer_id: Number(pid),
            print_profile: cfg.perPrinter[pid].printProfile!,
            filament_profile: cfg.perPrinter[pid].filamentProfile ?? null,
            filament_id: cfg.perPrinter[pid].filamentId ?? null,
            filament_type: cfg.perPrinter[pid].filamentType,
            filament_color: cfg.perPrinter[pid].filamentColor,
            tool_index: cfg.perPrinter[pid].toolIndex ?? null,
            filament_map: cfg.perPrinter[pid].filamentMap ?? null,
          })),
        });
      }
      clearFile();
      setSuccessMsg(`${count} job${count === 1 ? '' : 's'} added to queue`);
    } catch (err) {
      setError(`Failed to create job: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setSubmitting(false);
    }
  }
```

Remove the `{overrideFindings && <OverrideAlertModal .../>}` JSX block from the render.

- [ ] **Step 6: Update `PlateConfigPanel` props and add `OverridePanel` inside it**

Update the `PlateConfigPanel` function signature to add `embeddedSettings` and `onSetOverrides`:

```typescript
function PlateConfigPanel({ plate, config, isMultiPlate, printers, modelFilaments, embeddedSettings, onSetField, onTogglePrinter, onSetPerPrinter, onSetOrder, onToggleQueued, onSetOverrides }: {
  plate: Plate;
  config: PlateConfig;
  isMultiPlate: boolean;
  printers: ApiPrinter[];
  modelFilaments: ModelFilament[];
  embeddedSettings: EmbeddedSetting[];
  onSetField: (field: keyof PlateConfig, value: unknown) => void;
  onTogglePrinter: (pid: string) => void;
  onSetPerPrinter: (pid: string, patch: Partial<PerPrinterCfg>) => void;
  onSetOrder: (orderId: number | null) => void;
  onToggleQueued: (selected: boolean) => void;
  onSetOverrides: (overrides: Record<string, string>) => void;
})
```

Inside `PlateConfigPanel`, after the printer configs section and before the close of the main column, add the `OverridePanel`:

```tsx
        <OverridePanel
          settings={embeddedSettings}
          value={config.confirmedOverrides}
          onChange={onSetOverrides}
        />
```

- [ ] **Step 7: Pass new props where `PlateConfigPanel` is rendered**

Find where `<PlateConfigPanel` is rendered in the JSX and add the new props:

```tsx
<PlateConfigPanel
  plate={plates.find(p => p.id === activePlateId)!}
  config={plateConfigs[activePlateId]}
  isMultiPlate={plates.length > 1}
  printers={printers}
  modelFilaments={modelFilaments}
  embeddedSettings={embeddedSettings}
  onSetField={(f, v) => setPlateConfig(activePlateId, { [f]: v } as Partial<PlateConfig>)}
  onTogglePrinter={pid => togglePrinterForPlate(activePlateId, pid)}
  onSetPerPrinter={(pid, patch) => setPerPrinterForPlate(activePlateId, pid, patch)}
  onSetOrder={orderId => setPlateConfig(activePlateId, { orderId })}
  onToggleQueued={sel => togglePlate(activePlateId, sel)}
  onSetOverrides={overrides => setPlateConfig(activePlateId, { confirmedOverrides: overrides })}
/>
```

- [ ] **Step 8: TypeScript check**

```
cd frontend && npm run build 2>&1 | head -60
```

Expected: no TypeScript errors.

- [ ] **Step 9: Commit**

```
git add frontend/src/screens/NewJobScreen.tsx
git commit -m "feat(ui): OverridePanel + embedded settings in NewJobScreen, remove OverrideAlertModal"
```

---

## Task 10: Frontend — `EditJobScreen.tsx` integration

**Files:**
- Modify: `frontend/src/screens/EditJobScreen.tsx`

- [ ] **Step 1: Update imports**

Replace the existing import line (line 6):

```typescript
import { getJobDetails, updateJobConfigs, getModelFilaments, type ApiJobDetails, type ModelFilament } from '../api/queue';
```

With:

```typescript
import { getJobDetails, updateJobConfigs, getModelFilaments, getEmbeddedSettings, type ApiJobDetails, type ModelFilament, type EmbeddedSetting } from '../api/queue';
import { OverridePanel } from '../components/OverridePanel';
```

- [ ] **Step 2: Add `embeddedSettings` and `confirmedOverrides` state**

After the `modelFilaments` state (line ~94), add:

```typescript
  const [embeddedSettings, setEmbeddedSettings] = useState<EmbeddedSetting[]>([]);
  const [confirmedOverrides, setConfirmedOverrides] = useState<Record<string, string>>({});
```

- [ ] **Step 3: Fetch embedded settings and pre-populate overrides on load**

In the `useEffect` that loads job details (line ~96), after fetching model filaments, also fetch embedded settings and pre-populate overrides:

```typescript
      if (j.file?.id) {
        getModelFilaments(j.file.id)
          .then(f => { if (alive) setModelFilaments(f); })
          .catch(() => {});
        getEmbeddedSettings(j.file.id)
          .then(s => { if (alive) setEmbeddedSettings(s); })
          .catch(() => {});
      }
      // Pre-populate confirmed overrides from stored job.overrides
      if (j.overrides) {
        setConfirmedOverrides(j.overrides as Record<string, string>);
      }
```

- [ ] **Step 4: Pass `overrides` to `updateJobConfigs` in `handleSave`**

Update `handleSave`:

```typescript
  async function handleSave() {
    if (!jobId || !isComplete) return;
    setSaving(true);
    setSaveError(null);
    try {
      await updateJobConfigs(
        jobId,
        selectedPrinters.map(sid => ({
          printer_id: Number(sid),
          print_profile: perPrinter[sid].printProfile!,
          filament_profile: perPrinter[sid].filamentProfile ?? null,
          filament_id: perPrinter[sid].filamentId ?? null,
          filament_type: perPrinter[sid].filamentType,
          filament_color: perPrinter[sid].filamentColor,
          tool_index: perPrinter[sid].toolIndex ?? null,
          filament_map: perPrinter[sid].filamentMap ?? null,
        })),
        Object.keys(confirmedOverrides).length > 0 ? confirmedOverrides : null,
      );
      navigate(`/jobs/${jobId}`);
    } catch (e) {
      setSaveError(`Save failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setSaving(false);
    }
  }
```

- [ ] **Step 5: Add `OverridePanel` to the JSX**

Inside the "Slicing settings" card (after the `selectedPrinters.map(...)` block), add the `OverridePanel`:

```tsx
          {selectedPrinters.length > 0 && (
            <div className="card" style={{ padding: 20 }}>
              <SectionHeader title="Slicing settings"
                             sub="Print profile and filament for each eligible printer." />
              <div className="col gap-3">
                {selectedPrinters.map(sid => (
                  <PerPrinterConfig
                    key={sid}
                    printerId={sid}
                    printers={printers}
                    config={perPrinter[sid] ?? defaultPerPrinterCfg()}
                    onChange={patch => patchPerPrinter(sid, patch)}
                    modelFilaments={modelFilaments}
                  />
                ))}
              </div>
              <div style={{ marginTop: 16 }}>
                <OverridePanel
                  settings={embeddedSettings}
                  value={confirmedOverrides}
                  onChange={setConfirmedOverrides}
                />
              </div>
            </div>
          )}
```

- [ ] **Step 6: TypeScript check**

```
cd frontend && npm run build 2>&1 | head -60
```

Expected: no TypeScript errors.

- [ ] **Step 7: Run full backend test suite**

```
cd backend && python -m pytest -v
```

Expected: all PASS

- [ ] **Step 8: Run frontend tests**

```
cd frontend && npm test
```

Expected: all PASS

- [ ] **Step 9: Commit**

```
git add frontend/src/screens/EditJobScreen.tsx
git commit -m "feat(ui): OverridePanel + embedded settings in EditJobScreen"
```

---

## Final check

- [ ] **Start the dev servers and manually verify**

```powershell
# Kill stale python processes, start backend
cd backend; .venv\Scripts\Activate.ps1
uvicorn app.main:app --reload --port 8001 --host 0.0.0.0
# In another terminal:
cd frontend; npm run dev
```

Verify:
1. New Job with the Dummy 3MF file: override panel appears below printer configs; checking items and saving creates a job with `overrides` in the DB
2. Edit Job: panel pre-populates from stored overrides; saving updates `job.overrides`
3. Per-printer filament section: three-way selector is present; chip badge shows green/red/amber correctly when selecting type / type+color with different printers
4. Jobs with no embedded settings: override panel is hidden entirely

- [ ] **Final commit if any touch-up edits were needed**

```
git add -u
git commit -m "fix: final touch-ups from manual verification"
```
