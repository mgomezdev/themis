# Filament Tracking Design

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Track what filament is physically loaded in each printer slot, with a list-based data model that supports future multi-spool hardware.

**Architecture:** A new `loaded_filaments` JSON column on the `Printer` model stores a list of slot objects. Each slot carries inline display fields (`name`, `type`, `color`) plus a nullable `filament_id` for future linkage to the filament library. The existing PATCH endpoint handles writes; no new routes are needed.

**Tech Stack:** SQLAlchemy JSON column (backend), Pydantic model extension (backend), React + TypeScript (frontend), existing `PATCH /api/v1/printers/{id}` route.

---

## Data Model

### Backend — `Printer` model (`backend/app/models.py`)

Add one column:

```python
loaded_filaments: Mapped[list] = mapped_column(JSON, default=list)
```

Each item in the list is a **slot object**:

```json
{
  "slot": 0,
  "filament_id": null,
  "name": "Polymaker PolyTerra PLA",
  "type": "PLA",
  "color": "#3a3a3a"
}
```

Field semantics:
- `slot` — 0-based integer index. Single-spool printers always use slot 0. Future AMS-style units add slots 1–N.
- `filament_id` — nullable string. `null` means free-form entry. When the filament library backend exists, this will hold its ID for cross-referencing. `null` is always valid and must never cause an error.
- `name` — human-readable filament name (free text).
- `type` — material type string. Valid values: `PLA`, `PETG`, `ABS`, `ASA`, `PA-CF`, `PC`, `TPU`, `Other`. Stored as-is; no enum enforcement on the backend.
- `color` — hex color string (e.g. `"#3a3a3a"`).

`loaded_filaments` defaults to `[]`. A printer with no filament loaded has an empty list.

### API (`backend/app/api/routes/printers.py`)

**`PrinterUpdate`** gains one optional field:

```python
loaded_filaments: list[dict] | None = None
```

**`_to_dict`** includes the column:

```python
"loaded_filaments": p.loaded_filaments or [],
```

**`PATCH /api/v1/printers/{id}`** applies the update:

```python
if body.loaded_filaments is not None:
    printer.loaded_filaments = body.loaded_filaments
```

No new routes. No validation of slot structure beyond what Pydantic provides for `list[dict]` — the backend treats slot objects as opaque JSON and stores them as-is.

---

## Frontend Types (`frontend/src/data/types.ts`)

Add a new interface:

```typescript
export interface LoadedFilament {
  slot: number;
  filamentId?: string | null;
  name: string;
  type: string;
  color: string;
}
```

Extend the API `Printer` response type (the type used by the printers API module, not the mock `Printer` in `types.ts`) to include:

```typescript
loadedFilaments: LoadedFilament[];
```

The field is camelCased by the frontend API module (mapping from the snake_case JSON response).

---

## Frontend UI

### Printer card (read-only, `PrintersScreen.tsx`)

Each printer row shows a horizontal row of small spool swatches alongside the printer name. Reuse or inline the `SpoolSwatch` component from `FilamentsScreen.tsx`.

- If `loadedFilaments` is empty, show a faint `"— no filament"` text placeholder in place of swatches.
- For multiple slots, swatches appear left-to-right in slot order.
- Swatches are not interactive on the card.

### Edit / detail panel (editable, `PrintersScreen.tsx`)

A "Loaded filaments" section in the printer detail/edit panel. One row per slot:

```
Slot 1  [● #3a3a3a]  [PLA ▾]  [Polymaker PolyTerra PLA    ]  [×]
         Add slot +
```

Row fields (left to right):
- **Slot label** — "Slot N" (1-based display, 0-based in data).
- **Color** — `<input type="color">` paired with a hex text input. Both stay in sync.
- **Type** — `<select>` with options: `PLA`, `PETG`, `ABS`, `ASA`, `PA-CF`, `PC`, `TPU`, `Other`.
- **Name** — free-text `<input type="text">`.
- **Remove (×)** — removes the slot row from local state.

Controls:
- **"Add slot" button** — appends a new blank slot `{ slot: nextIndex, filamentId: null, name: "", type: "PLA", color: "#888888" }`.
- **Save** — the existing save action in the edit panel sends `PATCH /api/v1/printers/{id}` with `loaded_filaments` included. Re-indexes `slot` values 0-based before sending.

`filament_id` / `filamentId` is not exposed in the UI. It is always stored as `null` until the filament library backend exists.

---

## Tests

### Backend (`backend/tests/test_printers.py`)

1. **Create with slots** — POST a printer with `loaded_filaments` pre-filled; assert the response includes the slots.
2. **PATCH to update** — PATCH `loaded_filaments` on an existing printer; assert the new slots are returned.
3. **Defaults to empty list** — create a printer without `loaded_filaments`; assert the response field is `[]`.
4. **`filament_id: null` round-trips cleanly** — POST a printer with a slot where `filament_id` is `null`; assert no error and the slot is returned with `filament_id: null`.

### Frontend (`frontend/src/screens/PrintersScreen.test.tsx`)

1. **Swatch renders** — render a printer card with `loadedFilaments` populated; assert the color swatch is present.
2. **Empty placeholder** — render a printer card with `loadedFilaments: []`; assert the "— no filament" placeholder is shown.
3. **Slot editor add/remove** — open the edit panel, add a slot, remove it; assert local state updates correctly.
4. **PATCH fired on save** — fill in slot fields and save; assert `PATCH` is called with the correct `loaded_filaments` payload.
5. **`filamentId: null` renders without error** — render the edit panel with a slot where `filamentId` is `null`; assert no runtime error and the row is visible.
