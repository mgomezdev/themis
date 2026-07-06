# Project Builder — Design Spec

**Date:** 2026-06-27
**Repos:** `themis` (backend + frontend), `orca` (sidecar)
**Status:** Draft for review

---

## 1. Goal

Let users compose a "project" from multiple STL library files, assign quantities and filament profiles per part (including different materials for the same part), trigger auto-arrangement through OrcaSlicer, and save the resulting multi-plate 3MF back to the library — optionally queuing it as a print job.

---

## 2. Data Model

### 2.1 `projects` table

| Column | Type | Constraints | Notes |
|---|---|---|---|
| `id` | INTEGER | PK, autoincrement | |
| `name` | VARCHAR(255) | NOT NULL | User-supplied display name |
| `machine_uuid` | VARCHAR(36) | NOT NULL | Orca catalog machine UUID; used to derive bed_x/bed_y |
| `process_uuid` | VARCHAR(36) | NOT NULL | Orca catalog process UUID (for eventual slice) |
| `notes` | TEXT | nullable | Optional free-form notes |
| `result_file_id` | INTEGER | FK `uploaded_files.id`, nullable, ON DELETE SET NULL | Last arranged 3MF saved to library; null until first arrange |
| `created_at` | VARCHAR(32) | NOT NULL | ISO-8601 UTC |
| `updated_at` | VARCHAR(32) | NOT NULL | ISO-8601 UTC |

`machine_uuid` is stored (not `bed_x`/`bed_y`) so the project stays correct if the machine profile is updated. Bed dimensions are resolved at arrange-time by looking the UUID up in the Orca catalog.

### 2.2 `project_items` table

| Column | Type | Constraints | Notes |
|---|---|---|---|
| `id` | INTEGER | PK, autoincrement | |
| `project_id` | INTEGER | FK `projects.id`, ON DELETE CASCADE | |
| `file_id` | INTEGER | FK `uploaded_files.id`, ON DELETE RESTRICT | STL file in the library |
| `quantity` | INTEGER | NOT NULL, DEFAULT 1, CHECK ≥ 1 | Copies of this part |
| `filament_profile_uuid` | VARCHAR(36) | NOT NULL | Orca catalog filament UUID |
| `color_hex` | VARCHAR(7) | NOT NULL, DEFAULT `#FFFFFF` | Display / slot color, e.g. `#FF0000` |
| `sort_order` | INTEGER | NOT NULL, DEFAULT 0 | Display order within the project |

**Same file, different materials:** if a user wants 3× PartA in Blue PLA and 2× PartA in Red PLA, they create two `project_items` rows — both with the same `file_id`, one with `quantity=3` and the blue filament UUID, another with `quantity=2` and the red filament UUID. The arrangement engine expands each row to `quantity` individual STL copies before calling Orca.

---

## 3. Filament Slot Assignment Algorithm

OrcaSlicer's `project_settings.config` encodes filament info as parallel arrays indexed by slot number (1-based). The slot assignment must be deterministic so that re-arranging the same project produces the same slot mapping.

**Algorithm (runs inside `POST /api/v1/projects/{id}/arrange` before calling Orca):**

1. Collect all `project_items` rows for the project, ordered by `sort_order ASC, id ASC`.
2. Build a deduplicated ordered list of `(filament_profile_uuid, color_hex)` pairs — first occurrence order determines slot number. Two items with the same UUID and color get the same slot. Two items with the same UUID but different colors get different slots (color is user intent).
3. Assign slot indices 1..N to the unique pairs. Record the mapping: `{ (uuid, color) → slot_index }`.
4. For each `project_item` record its slot index. This slot index is:
   - Used when building the `model_settings.config` XML (the `extruder` attribute on each 3MF object).
   - Passed to `POST /api/profiles/merged-config` as the ordered list of `filament_uuids` (slot 1 → index 0, slot 2 → index 1, etc.). This is how OrcaSlicer knows what temperatures/settings go with each slot.

**Example:**
- Item A: PLA-Blue UUID `u1`, color `#0000FF`, qty 3 → slot 1
- Item B: PLA-Red UUID `u2`, color `#FF0000`, qty 2 → slot 2
- Item C: PLA-Blue UUID `u1`, color `#0000FF`, qty 1 → slot 1 (same as A)

Ordered filament UUID list passed to Orca: `["u1", "u2"]` (slot 1 first, slot 2 second).

---

## 4. Orca Sidecar — Arrangement Strategy

Rather than adding a new endpoint with N × identical STL files, the Themis backend constructs a combined multi-material 3MF itself (via `ProjectPackBuilder`) and sends it to the existing `POST /api/arrange` endpoint. This avoids changing the Orca API surface.

The key: OrcaSlicer reads extruder (slot) assignments from `Metadata/model_settings.config`. If Themis embeds this correctly in the input 3MF, OrcaSlicer preserves slot assignments through arrangement.

**Timeout:** Increase `POST /api/arrange` on the sidecar from 35 s to `ARRANGE_TIMEOUT_SECONDS` (default 120). Themis uses an httpx call timeout of 130 s.

### What `ProjectPackBuilder` Must Build

Given N project items (each with file_id, quantity, slot_index), construct a 3MF that:

1. Contains one `<object>` per copy of each STL (N_total = sum of all quantities), each with a unique `objectid`.
2. `Metadata/model_settings.config` carries per-object extruder assignments:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<config>
  <object id="1"><metadata key="extruder" value="1"/></object>
  <object id="2"><metadata key="extruder" value="1"/></object>
  <object id="3"><metadata key="extruder" value="2"/></object>
</config>
```
3. `Metadata/project_settings.config` carries filament parallel arrays:
```json
{
  "printable_area": ["0x0", "220x0", "220x220", "0x220"],
  "printable_height": "300",
  "filament_settings_id": ["Elegoo PLA @ECC", "Elegoo PLA @ECC"],
  "filament_colour": ["#0055AA", "#FF0000"],
  "filament_type": ["PLA", "PLA"],
  "from": "user"
}
```

`printable_area` corners are formatted as `"<x>x<y>"` strings. Bed dimensions come from the machine profile in the Orca catalog.

**Multi-copy mechanism:** Parse the STL once and emit N `<object>` elements with identical geometry but separate `objectid` values. OrcaSlicer treats them as independent parts for arrangement.

---

## 5. Themis Backend API Routes

All routes use prefix `/api/v1/projects`.

### 5.1 Project CRUD

| Method | Path | Request | Response | Notes |
|---|---|---|---|---|
| `GET` | `/api/v1/projects` | — | `list[ProjectOut]` | All projects, newest first |
| `POST` | `/api/v1/projects` | `ProjectCreate` | `ProjectOut` (201) | Creates project with no items |
| `GET` | `/api/v1/projects/{id}` | — | `ProjectOut` | 404 if not found |
| `PATCH` | `/api/v1/projects/{id}` | `ProjectPatch` | `ProjectOut` | Update name / machine_uuid / process_uuid / notes |
| `DELETE` | `/api/v1/projects/{id}` | — | `{"deleted": id}` | Cascades to items; does not delete result_file_id |

**`ProjectCreate`:**
```json
{
  "name": "Gridfinity Tray Set",
  "machine_uuid": "a7f3c2e1-...",
  "process_uuid": "b8e4d3f2-...",
  "notes": null
}
```

**`ProjectOut`:**
```json
{
  "id": 1,
  "name": "Gridfinity Tray Set",
  "machine_uuid": "a7f3c2e1-...",
  "process_uuid": "b8e4d3f2-...",
  "notes": null,
  "result_file_id": null,
  "created_at": "2026-06-27T10:00:00Z",
  "updated_at": "2026-06-27T10:00:00Z",
  "items": []
}
```

### 5.2 Project Items

| Method | Path | Request | Response | Notes |
|---|---|---|---|---|
| `GET` | `/api/v1/projects/{id}/items` | — | `list[ProjectItemOut]` | Ordered by sort_order |
| `POST` | `/api/v1/projects/{id}/items` | `ProjectItemCreate` | `ProjectItemOut` (201) | |
| `PUT` | `/api/v1/projects/{id}/items/{item_id}` | `ProjectItemUpdate` | `ProjectItemOut` | Full replace of mutable fields |
| `DELETE` | `/api/v1/projects/{id}/items/{item_id}` | — | `{"deleted": item_id}` | |
| `PUT` | `/api/v1/projects/{id}/items/reorder` | `[{"id": n, "sort_order": k}]` | `list[ProjectItemOut]` | Batch reorder |

**`ProjectItemOut`:**
```json
{
  "id": 7,
  "project_id": 1,
  "file_id": 42,
  "file_name": "gridfinity_2x1.stl",
  "quantity": 3,
  "filament_profile_uuid": "c9f5e4g3-...",
  "filament_display_name": "Elegoo PLA @ECC",
  "color_hex": "#0055AA",
  "sort_order": 0
}
```

`file_name` resolved from `uploaded_files`; `filament_display_name` resolved from Orca catalog (null if catalog unavailable).

### 5.3 Arrange

| Method | Path | Request | Response | Notes |
|---|---|---|---|---|
| `POST` | `/api/v1/projects/{id}/arrange` | `{}` | `ArrangeOut` | Synchronous; blocks up to 130 s |

**`ArrangeOut`:**
```json
{
  "project_id": 1,
  "result_file_id": 99,
  "plate_count": 3,
  "file": {
    "id": 99,
    "original_filename": "project-gridfinity-tray-set.3mf",
    "plate_count": 3
  }
}
```

**Arrange endpoint flow:**
1. Fetch project + items (404 / 422 if empty).
2. Check `ORCA_SIDECAR_URL` configured (422 if not).
3. Fetch Orca catalog; resolve `machine_uuid` → `bed_size_x`, `bed_size_y` (422 if not found).
4. Run slot assignment algorithm.
5. Resolve each item's STL path from `UploadedFile.stored_path` (422 if missing or not `.stl`).
6. In a temp directory: call `ProjectPackBuilder.build()` → `combined.3mf`.
7. Call `OrcaSidecarClient.arrange(combined.3mf, timeout=130.0)` → `arranged_bytes`.
8. Write bytes to `<library_dir>/Projects/<slugified-name>.3mf`. If `result_file_id` exists and is not referenced by an active job, overwrite; otherwise create new record.
9. Update `project.result_file_id` + `updated_at`. Commit.
10. Background: `regen_file_thumbnails(result_file_id)`.
11. Return `ArrangeOut`.

**Error responses:**

| Code | Condition |
|---|---|
| 422 | `ORCA_SIDECAR_URL` not set |
| 422 | Machine UUID not in catalog |
| 422 | Project has no items |
| 422 | STL file missing from disk |
| 400 | File is not an STL |
| 502 | Sidecar `SidecarError` (non-timeout) |
| 504 | Sidecar `SidecarError` containing "timed out" |

### 5.4 Queue Shortcut

| Method | Path | Request | Response | Notes |
|---|---|---|---|---|
| `POST` | `/api/v1/projects/{id}/queue` | `ProjectQueueRequest` | `JobOut` (201) | Creates job from result_file_id |

Returns 409 if `result_file_id` is null.

### 5.5 Orca Catalog Proxy

| Method | Path | Response | Notes |
|---|---|---|---|
| `GET` | `/api/v1/orca/catalog` | catalog dict | Proxies sidecar catalog for frontend |

Thin proxy in a new `orca.py` router. Returns 503 if sidecar not configured, 502 if unreachable.

---

## 6. Frontend UX Flow

### 6.1 Navigation

New "Projects" nav item in Sidebar, between Files and Settings.

### 6.2 Project List Screen (`/projects`)

Card grid showing all projects. Each card: name, item count, result thumbnail (if arranged), Edit and Arrange buttons. Empty state: "No projects yet — create one to start batching parts."

### 6.3 Project Builder Screen (`/projects/new` and `/projects/:id`)

Two-column layout:

**Left (30%):** Folder tree (STL files only). Clicking a file adds it to the item list.

**Right (70%):**
- Project name input (required)
- Machine profile picker (from Orca catalog via proxy)
- Process profile picker (filtered by selected machine's `compatible_printers`)
- Item table:
  - Columns: drag handle · file name · quantity stepper · filament combobox · color swatch · remove button
  - "Same file, different material": "+ Add variation" button per row duplicates row with same `file_id`
- Footer: "Save" and "Save & Arrange"

**Create flow:** POST `/api/v1/projects`, then POST each item, navigate to `/projects/:id`.
**Edit flow:** PATCH for header changes; incremental PUT/DELETE per item row.

### 6.4 Arrange Result Panel

On success: plate thumbnail strip, plate count, "Add to Queue" and "View in Files" buttons. If thumbnails not yet ready, poll `GET /api/v1/files/{id}` every 3 s (max 10 retries). On error: inline banner with human-readable message and Retry button.

| Status | User message |
|---|---|
| 422 no items | "Add at least one part before arranging." |
| 422 machine | "Machine profile not found — update project settings." |
| 422 STL missing | "One or more STL files are missing. Remove and re-add them." |
| 502 | "Orca sidecar is offline. Check the container." |
| 504 | "Arrangement timed out. Try fewer parts or reduce quantities." |

---

## 7. Multi-Material in OrcaSlicer 3MF

OrcaSlicer reads extruder assignments from `Metadata/model_settings.config`. The `id` attribute matches `objectid` in `3D/3dmodel.model`. The `extruder` value is 1-based and references position in `filament_settings_id`.

`filament_settings_id` values are filament profile **names** (not UUIDs). `ProjectPackBuilder` resolves UUIDs → display names from the catalog before writing.

When OrcaSlicer runs `--arrange 1`, it redistributes objects across plates but preserves `model_settings.config` extruder assignments in the output.

---

## 8. Key Constraints

- STL-only in v1. 3MF sources would conflict with `ProjectPackBuilder`'s own `model_settings.config`.
- Results saved to `<library_dir>/Projects/` folder.
- Catalog cached 5 minutes in projects route (separate from `SlicerService` cache).
- Active-job guard before overwriting `result_file_id`.
- Frontend uses `GET /api/v1/orca/catalog` proxy (not direct sidecar calls).
