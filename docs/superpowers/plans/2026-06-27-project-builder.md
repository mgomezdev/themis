# Project Builder — Implementation Plan

**Date:** 2026-06-27
**Repos:** `themis` (branch: `feat/project-builder`), `orca` (branch: `feat/pack-endpoint`)
**Spec:** `docs/superpowers/specs/2026-06-27-project-builder-design.md`

Stories are ordered by dependency. Each story can be reviewed and merged independently.

---

## Story 1 — Orca: Increase arrange timeout + verify multi-material passthrough

**Scope:** `orca` repo only.

### Files to modify
- `orca/app/main.py`

### Changes
1. Add `ARRANGE_TIMEOUT = int(os.environ.get("ARRANGE_TIMEOUT_SECONDS", "120"))` alongside `SLICE_TIMEOUT`.
2. In `auto_arrange_3mf()`, replace hardcoded `timeout=35.0` with `timeout=float(ARRANGE_TIMEOUT)`.
3. Update the 408 error message to reference the new timeout value.

The endpoint already copies all 3MF zip entries verbatim — `model_settings.config` is preserved because the input 3MF is passed directly to OrcaSlicer. No code change needed for passthrough; add a test to confirm.

### Tests to write
- `orca/tests/test_arrange_timeout.py`
  - `test_arrange_reads_timeout_env` — set env var, assert constant resolves.
- `orca/tests/test_arrange_model_settings_passthrough.py`
  - `test_model_settings_preserved_through_arrange` — 3MF with `model_settings.config`; mock subprocess returns same archive; assert output contains the file with extruder attribute intact.

### Acceptance criteria
- `POST /api/arrange` returns 408 after `ARRANGE_TIMEOUT_SECONDS` when subprocess mocked to hang.
- A 3MF with `model_settings.config` passed in → present in response.

---

## Story 2 — Themis: `OrcaSidecarClient.arrange()` method

**Scope:** `themis/backend` only.

### Files to modify
- `themis/backend/app/services/orca_sidecar_client.py`

### Changes

```python
def arrange(
    self,
    threemf_path: Path,
    arrange: bool = True,
    orient: bool = True,
    timeout: float = 130.0,
) -> bytes:
    """POST /api/arrange → arranged 3MF bytes. Raises SidecarError on failure."""
    try:
        with open(threemf_path, "rb") as fh:
            r = self._client.post(
                "/api/arrange",
                files={"file": (threemf_path.name, fh, "application/octet-stream")},
                data={"arrange": "1" if arrange else "0",
                      "orient": "1" if orient else "0"},
                timeout=timeout,
            )
    except httpx.HTTPError as e:
        raise SidecarError(f"arrange request failed: {e}") from e
    if r.status_code == 408:
        raise SidecarError("arrange timed out on sidecar")
    if r.status_code != 200:
        raise SidecarError(f"arrange returned {r.status_code}: {r.text[:300]}")
    return r.content
```

### Tests to write
Add to `themis/backend/tests/services/test_orca_sidecar_client.py`:
- `test_arrange_returns_bytes` — 200 response → bytes returned.
- `test_arrange_raises_on_408` — 408 → `SidecarError` matching "timed out".
- `test_arrange_raises_on_400` — 400 → `SidecarError`.
- `test_arrange_sends_correct_form_fields` — assert `arrange=1` and `orient=1` fields present.

### Acceptance criteria
- All 4 new tests pass.

---

## Story 3 — Themis: DB migration + Pydantic schemas + CRUD routes

**Scope:** `themis/backend` only.

### Files to create
- `themis/backend/app/api/routes/projects.py`

### Files to modify
- `themis/backend/app/models.py` — add `Project` and `ProjectItem` ORM classes
- `themis/backend/app/database.py` — note new tables created by `create_all`
- `themis/backend/app/main.py` — include projects router

### ORM models

```python
class Project(Base):
    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    machine_uuid: Mapped[str] = mapped_column(String(36))
    process_uuid: Mapped[str] = mapped_column(String(36))
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    result_file_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("uploaded_files.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[str] = mapped_column(String(32))
    updated_at: Mapped[str] = mapped_column(String(32))

class ProjectItem(Base):
    __tablename__ = "project_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"))
    file_id: Mapped[int] = mapped_column(
        ForeignKey("uploaded_files.id", ondelete="RESTRICT"))
    quantity: Mapped[int] = mapped_column(default=1)
    filament_profile_uuid: Mapped[str] = mapped_column(String(36))
    color_hex: Mapped[str] = mapped_column(String(7), default="#FFFFFF")
    sort_order: Mapped[int] = mapped_column(default=0)
```

New tables are created by `Base.metadata.create_all` — no `_ALTERS` entry needed.

### Router routes
- `GET /api/v1/projects` — all projects newest first, items nested.
- `POST /api/v1/projects` — create; return with empty items.
- `GET /api/v1/projects/{id}` — 404 if missing.
- `PATCH /api/v1/projects/{id}` — update name/machine/process/notes; bump `updated_at`.
- `DELETE /api/v1/projects/{id}` — cascade to items; don't touch `result_file_id` file.
- `GET /api/v1/projects/{id}/items`
- `POST /api/v1/projects/{id}/items` — validate `file_id` exists; validate `quantity >= 1`.
- `PUT /api/v1/projects/{id}/items/{item_id}` — validate ownership.
- `DELETE /api/v1/projects/{id}/items/{item_id}` — validate ownership.
- `PUT /api/v1/projects/{id}/items/reorder` — batch `sort_order` update.

`filament_display_name` returns `null` in this story (catalog wired in Story 4).

### Tests to write
`themis/backend/tests/api/test_projects_api.py`:
- `test_create_project`
- `test_get_project_not_found`
- `test_list_projects_empty`
- `test_patch_project_name`
- `test_delete_project`
- `test_add_item_to_project`
- `test_add_item_missing_file`
- `test_add_item_quantity_zero_rejected`
- `test_update_item`
- `test_delete_item`
- `test_reorder_items`
- `test_delete_project_cascades_items`

### Acceptance criteria
- All 12 tests pass.
- Tables created by `init_db()` on fresh in-memory SQLite.

---

## Story 4 — Themis: `ProjectPackBuilder` + arrange + queue endpoints

**Scope:** `themis/backend` only. Depends on Stories 1–3.

### Files to create
- `themis/backend/app/services/project_pack_builder.py`
- `themis/backend/tests/services/test_project_pack_builder.py`
- `themis/backend/tests/api/test_projects_arrange.py`

### Files to modify
- `themis/backend/app/api/routes/projects.py` — add `POST /{id}/arrange` and `POST /{id}/queue`

### `ProjectPackBuilder`

```python
class ProjectPackBuilder:
    def build(
        self,
        items: list[ProjectItemRow],
        # Each row: file_path, quantity, slot_index
        bed_x: float,
        bed_y: float,
        filament_info: list[FilamentSlot],
        # FilamentSlot: uuid, display_name, filament_type, color_hex (ordered by slot)
        out_path: Path,
    ) -> None: ...
```

Implementation:
1. Assign sequential `objectid`s. For each item, parse STL and emit N `<object>` elements (N = quantity) with identical geometry but separate IDs.
2. Write `Metadata/model_settings.config` with per-object `<metadata key="extruder" value="{slot_index}"/>`.
3. Write `Metadata/project_settings.config` with `printable_area` corners (formatted as `"<x>x<y>"`), `filament_settings_id`, `filament_colour`, `filament_type` arrays (ordered by slot).
4. Write standard `[Content_Types].xml` and `_rels/.rels`.

### Arrange endpoint flow
1. Fetch project + items. 404/422 guards.
2. `get_orca_sidecar_url()` — 422 if not set.
3. Fetch catalog (module-level 5-min cache). Resolve machine → `bed_size_x`, `bed_size_y`. 422 if not found.
4. Slot assignment algorithm.
5. Resolve STL paths. 422/400 on bad files.
6. `tempfile.TemporaryDirectory()` → `ProjectPackBuilder.build()` → `combined.3mf`.
7. `await asyncio.to_thread(client.arrange, Path("combined.3mf"), timeout=130.0)` → bytes.
8. Active-job guard before overwriting `result_file_id`:
   ```python
   active = (await session.execute(
       select(Job.id).where(
           Job.uploaded_file_id == project.result_file_id,
           Job.status.in_(ACTIVE_JOB_STATUSES)
       ).limit(1)
   )).first()
   ```
   If active: create new file record. If not: overwrite.
9. Write bytes to `<library_dir>/Projects/<slugified-name>.3mf`. Create/update `UploadedFile`.
10. Update `project.result_file_id` + `updated_at`. Commit.
11. Background: `regen_file_thumbnails(result_file_id)`.
12. Return `ArrangeOut`.

**Error mapping:**
- `SidecarError` containing "timed out" → 504
- Other `SidecarError` → 502

### Queue shortcut
Extract `_create_job()` helper from `jobs.py`. Call it from both `POST /api/v1/jobs` and `POST /api/v1/projects/{id}/queue`. Return 409 if `result_file_id` is null.

### Tests to write

`test_project_pack_builder.py`:
- `test_build_single_item_single_slot`
- `test_build_two_items_two_slots`
- `test_build_quantity_three`
- `test_build_project_settings_contains_bed_dimensions`
- `test_build_filament_arrays_ordered_by_slot`
- `test_build_output_is_valid_zip`

`test_projects_arrange.py`:
- `test_arrange_returns_200_with_result_file`
- `test_arrange_404_project_not_found`
- `test_arrange_422_no_items`
- `test_arrange_422_sidecar_not_configured`
- `test_arrange_502_sidecar_unreachable`
- `test_arrange_504_sidecar_timeout`
- `test_arrange_422_stl_file_missing`
- `test_queue_project_409_not_arranged`
- `test_queue_project_creates_job`

### Acceptance criteria
- All 15 tests pass.
- `POST /api/v1/projects/1/arrange` with mocked sidecar → `result_file_id` points to `Projects/` folder file.
- Arranging twice updates the existing record (not an orphan).
- `POST /api/v1/projects/1/queue` with valid project → 201 job record.

---

## Story 5 — Frontend: Project list screen

**Scope:** `themis/frontend` only. Depends on Story 3.

### Files to create
- `themis/frontend/src/api/projects.ts`
- `themis/frontend/src/screens/ProjectsScreen.tsx`
- `themis/frontend/src/screens/ProjectsScreen.test.tsx`

### Files to modify
- `themis/frontend/src/App.tsx` — add `/projects` and `/projects/:id` routes
- `themis/frontend/src/components/Sidebar.tsx` — add "Projects" nav item (layers icon)

### `api/projects.ts`
TypeScript types: `Project`, `ProjectItem`, `ArrangeOut`. Functions: `getProjects()`, `getProject(id)`, `createProject(body)`, `patchProject(id, body)`, `deleteProject(id)`, `addProjectItem(projectId, body)`, `updateProjectItem(projectId, itemId, body)`, `deleteProjectItem(projectId, itemId)`, `reorderProjectItems(projectId, items)`, `arrangeProject(projectId)`, `queueProject(projectId, body)`. Hook: `useProjects()`.

### `ProjectsScreen`
Card grid. Each card: name, "N parts · M copies" summary, thumbnail (placeholder if no `result_file_id`). Topbar "+" button → `/projects/new`. Card actions: Edit, Arrange (inline with loading state), Delete (with confirmation).

### Tests
- `renders empty state when no projects`
- `renders project cards`
- `delete project calls api and refreshes`
- `new project button navigates to /projects/new`

### Acceptance criteria
- Projects nav item in sidebar.
- Empty state shown with no data.
- Cards render with correct names and summaries.

---

## Story 6 — Frontend: Project builder (file picker + item table)

**Scope:** `themis/frontend` only. Depends on Stories 3 and 5.

### Files to create
- `themis/frontend/src/screens/ProjectBuilderScreen.tsx`
- `themis/frontend/src/screens/ProjectBuilderScreen.test.tsx`
- `themis/frontend/src/components/FilamentProfilePicker.tsx`
- `themis/frontend/src/api/orca.ts` (for catalog proxy)

### Files to modify
- `themis/backend/app/api/routes/orca.py` (new file) — `GET /api/v1/orca/catalog`
- `themis/backend/app/main.py` — include orca router

### Layout
Two-column. Left: folder tree (STL files only, reuse extracted `FolderTreeNode` component). Right: name input, machine picker, process picker, item table, footer actions.

### Item table columns
Drag handle · file name · qty stepper (min 1, max 99) · `FilamentProfilePicker` combobox · `<input type="color">` swatch · "+ Add variation" · remove.

### FilamentProfilePicker
Combobox fetching from `GET /api/v1/orca/catalog`, filtering by `filament_type` and name. Shows `filament_type` badge + display name.

### Save flow
New: POST project → POST items in order → navigate to `/projects/:id`.
Edit: PATCH header changes + incremental PUT/DELETE per item (not batch replace).

### "+ Add variation"
Duplicates row with same `file_id`, incremented `sort_order`, empty filament selection.

### Tests
- `renders empty item list for new project`
- `clicking an STL file adds it to item list`
- `quantity stepper increments and decrements`
- `save button disabled when name is empty`
- `add variation button duplicates item row`
- `remove button removes item from list`

### Acceptance criteria
- `/projects/new` shows empty builder.
- Clicking STL appends row to item table.
- Filament combobox populates from catalog proxy.
- Saving with two items creates project + two item records.
- `/projects/:id` pre-populates all fields.

---

## Story 7 — Frontend: Arrange action + result preview + Add to Queue

**Scope:** `themis/frontend` only. Depends on Stories 4–6.

### Files to modify
- `themis/frontend/src/screens/ProjectBuilderScreen.tsx` — arrange button, result panel
- `themis/frontend/src/screens/ProjectsScreen.tsx` — arrange button on cards

### Arrange flow
1. "Save & Arrange": save then call `arrangeProject(id)`.
2. Loading overlay: "Arranging across plates — this may take up to 2 minutes."
3. On success: result panel with plate thumbnail strip, plate count, "Add to Queue" and "View in Files" buttons.
4. On error: inline banner with human-readable message + Retry button.

### Plate thumbnail strip
```tsx
function PlateThumbnailStrip({ plates }) {
  return (
    <div style={{ display: 'flex', gap: 8, overflowX: 'auto' }}>
      {plates.map(p => (
        <div key={p.plate_number}>
          <img src={p.thumbnail_url} alt={`Plate ${p.plate_number}`}
               style={{ width: 120, height: 120, objectFit: 'contain' }} />
          <div>Plate {p.plate_number}</div>
        </div>
      ))}
    </div>
  );
}
```

If thumbnails empty: poll `GET /api/v1/files/{id}` every 3 s, max 10 retries.

### Error message map

| Code | Message |
|---|---|
| 422 (no items) | "Add at least one part before arranging." |
| 422 (machine) | "Machine profile not found — update project settings." |
| 422 (STL missing) | "One or more STL files are missing. Remove and re-add them." |
| 502 | "Orca sidecar is offline. Check the container." |
| 504 | "Arrangement timed out. Try fewer parts or reduce quantities." |
| other | "Arrangement failed: {detail}" |

### "Add to Queue"
```ts
navigate('/queue/new', { state: { fileId: result_file_id } });
```
`NewJobScreen` already handles `fileId` in location state — no changes to `NewJobScreen` needed.

### Tests
- `shows loading overlay during arrange`
- `displays plate thumbnails on successful arrange`
- `shows error banner on 504 timeout`
- `add to queue button navigates to /queue/new with fileId in state`
- `view in files button navigates to /files`
- `retry button re-submits arrange request`

### Acceptance criteria
- "Save & Arrange" on a valid project calls arrange and shows result panel.
- Plate thumbnails load within 15 s of completion.
- "Add to Queue" pre-fills `NewJobScreen` with result 3MF.
- 504 shows timeout message without crash.

---

## Cross-cutting Notes

- **STL-only v1.** 3MF sources would conflict with `ProjectPackBuilder`'s own `model_settings.config`. Support in v2.
- **`Projects` library folder.** Arrange endpoint creates `<library_dir>/Projects/` if not present.
- **Catalog cache in `projects.py`.** Module-level `_catalog_cache` + `_catalog_ts` with 5-min TTL. Separate from `SlicerService` cache.
- **`filament_display_name` wiring.** After Story 4, look up UUID in cached catalog during `GET /api/v1/projects/{id}`. Return `null` if catalog cold.
- **Testing isolation.** All backend tests use in-memory SQLite via existing `conftest.py`. Patch `get_orca_sidecar_url` and `OrcaSidecarClient`. No running sidecar required.
