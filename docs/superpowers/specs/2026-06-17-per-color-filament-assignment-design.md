# Per-Color Filament Assignment for Multi-Material Jobs — Design Spec

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Let operators assign each model color in a multi-material job to either a specific tool slot or a Spoolman catalog filament; the queue engine resolves catalog filament → slot at dispatch/slice time.

**Architecture:** Extend the existing `filamentMap` entries in `PerPrinterCfg` to carry an optional `filament_id`, `filament_type`, and `filament_color` (from the Spoolman `ApiFilament`) instead of a slot index. The UI gets a unified dropdown per color row (slots + catalog). The backend `FilamentMapEntry` model gains matching optional fields. Queue engine extends its eligibility check and slot-resolution logic to match catalog entries by type+color against loaded slots — the same mechanism as the existing single-filament `_filament_mismatch` check. No new job status or DB columns needed.

**Tech Stack:** React/TypeScript frontend (`PerPrinterConfig.tsx`), FastAPI/Pydantic backend (`queue.py` models + `queue_engine.py`).

---

## System context

A multi-material plate has N color slots (`modelFilaments`). Today, `PerPrinterConfig` lets the operator map each color index to a physical tool slot (T0, T1…). The color → slot mapping is stored in `job_printer_configs.filament_map` and consumed by the queue engine to build `multi_presets` for OrcaSlicer.

This feature adds a second assignment mode per color: instead of pinning a slot index, the operator picks a filament from the Spoolman catalog. At dispatch time, the queue engine scans the printer's `loaded_filaments` to find which slot has that filament loaded, and uses that slot index. If no slot matches, the printer is considered ineligible for the job at that moment — the same "temporarily ineligible" behavior as the existing `_filament_mismatch` check for single-filament constraints.

Filament profiles (for slicing) come from the printer's loaded slot configuration, not the job — no per-color profile field is needed in the job.

---

## Scope

Only the multi-filament mapping path is changed — the branch inside `PerPrinterConfig` active when `modelFilaments.length > 1`. The single-filament constraint path (defer / type-only / type-color) and the multi-tool selection path (single model filament on a multi-slot printer) are unchanged.

---

## Data model

### Frontend: `PerPrinterCfg.filamentMap`

**Before:**
```ts
filamentMap: { model_filament: number; tool_index: number }[] | null
```

**After:**
```ts
filamentMap: {
  model_filament: number;
  tool_index: number | null;
  filament_id: number | null;
  filament_type: string | null;   // material, e.g. "PLA+"
  filament_color: string | null;  // hex with #, e.g. "#FFFFFF"
}[] | null
```

Exactly one of `tool_index` / `filament_id` is non-null per entry. A slot assignment sets `tool_index` and leaves the filament fields null. A catalog assignment sets `filament_id`, `filament_type`, and `filament_color` (populated at selection time from the `ApiFilament`) and leaves `tool_index: null`. The queue engine uses `filament_type` + `filament_color` for slot matching — no runtime Spoolman API call needed.

### Backend: `FilamentMapEntry` (in `app/routers/queue.py` or wherever `PrinterConfigInput` is defined)

**Before:**
```python
class FilamentMapEntry(BaseModel):
    model_filament: int
    tool_index: int
```

**After:**
```python
class FilamentMapEntry(BaseModel):
    model_filament: int
    tool_index: int | None = None
    filament_id: int | None = None
    filament_type: str | None = None   # e.g. "PLA+"
    filament_color: str | None = None  # hex with #, e.g. "#FFFFFF"
```

No DB schema change — `filament_map` is stored as JSON in `job_printer_configs`.

---

## UI changes (`src/components/PerPrinterConfig.tsx`)

### Dropdown encoding

Each color row's `<select>` uses string values to distinguish slot vs catalog assignments:
- Slot: `"t:0"`, `"t:1"`, … (tool index)
- Catalog filament: `"f:7"`, `"f:19"`, … (filament ID)

A helper converts between the dropdown string value and the `filamentMap` entry:

```ts
function encodeAssignment(entry: { tool_index: number | null; filament_id: number | null }): string {
  if (entry.tool_index !== null) return `t:${entry.tool_index}`;
  if (entry.filament_id !== null) return `f:${entry.filament_id}`;
  return `t:0`; // fallback
}

// decodeAssignment is only used to parse the dropdown value back to an ID;
// the full filament_type/filament_color are looked up from the filaments list at selection time.
function decodeAssignment(val: string): { tool_index: number | null; filament_id: number | null } {
  if (val.startsWith('t:')) return { tool_index: Number(val.slice(2)), filament_id: null };
  if (val.startsWith('f:')) return { tool_index: null, filament_id: Number(val.slice(2)) };
  return { tool_index: 0, filament_id: null };
}
```

### Dropdown structure and `onChange` contract

```tsx
<select value={encodeAssignment(entry)} onChange={e => handleAssignmentChange(f.index, e.target.value)}>
  <optgroup label="Slots">
    {slots.map((s, i) => (
      <option key={i} value={`t:${i}`}>T{i} · {s.type || '—'}{s.name ? ` (${s.name})` : ''}</option>
    ))}
  </optgroup>
  {spoolmanActive && filaments.length > 0 && (
    <optgroup label="Catalog">
      {filaments.map(fil => (
        <option key={fil.id} value={`f:${fil.id}`}>{filamentDisplayName(fil)} · {fil.material}</option>
      ))}
    </optgroup>
  )}
</select>
```

`handleAssignmentChange` replaces the entry in the current map:

```ts
function handleAssignmentChange(modelFilament: number, val: string) {
  const base = currentMap; // current filamentMap or identity map
  const newMap = base.filter(e => e.model_filament !== modelFilament);
  if (val.startsWith('t:')) {
    newMap.push({ model_filament: modelFilament, tool_index: Number(val.slice(2)), filament_id: null, filament_type: null, filament_color: null });
  } else {
    const fid = Number(val.slice(2));
    const fil = filaments.find(f => f.id === fid)!;
    newMap.push({
      model_filament: modelFilament,
      tool_index: null,
      filament_id: fid,
      filament_type: fil.material,
      filament_color: fil.color_hex ? `#${fil.color_hex}` : null,
    });
  }
  newMap.sort((a, b) => a.model_filament - b.model_filament);
  onChange({ filamentMap: newMap });
}
```

### Default assignment (new entries / identity map)

When building the identity map (no saved `filamentMap`), default to slot assignment as today:
```ts
{ model_filament: mf.index, tool_index: Math.min(mf.index - 1, slots.length - 1), filament_id: null }
```

### Preview badge

Below each row where `filament_id` is set, show a badge using the same type+color match the queue engine will use:

```ts
function findLoadedSlotForEntry(
  entry: { filament_type: string | null; filament_color: string | null },
  slots: LoadedFilament[],
): number | null {
  if (!entry.filament_type) return null;
  const reqType = entry.filament_type.toLowerCase();
  const reqColor = (entry.filament_color ?? '').replace('#', '').toLowerCase();
  const idx = slots.findIndex(s => {
    if ((s.type ?? '').toLowerCase() !== reqType) return false;
    if (!reqColor) return true;
    return (s.color ?? '').replace('#', '').toLowerCase() === reqColor;
  });
  return idx >= 0 ? idx : null;
}
```

Badge:
- `tool_index` assignment: no badge
- `filament_id` assignment, slot found at index `n`: `✓ T{n} loaded now` (green)
- `filament_id` assignment, no slot: `⚠ {filament_type} not loaded — will block at slice` (amber)

### `useFilaments` call

`PerPrinterConfig` already calls `useFilaments(spoolmanActive)` for the single-filament catalog. No new hook call needed — reuse the existing `filaments` list.

---

## Queue engine changes (`app/services/queue_engine.py`)

### Eligibility check

Extend the existing filament eligibility logic (called per printer before dispatch) to handle multi-filament jobs with catalog entries. No Spoolman API call needed — `filament_type` and `filament_color` are stored on the entry at job creation time.

```python
def _find_slot_for_filament(filament_type: str, filament_color: str | None, loaded: list[dict]) -> int | None:
    """Return loaded slot index matching type (+color if provided), or None."""
    req_type = filament_type.lower()
    req_color = (filament_color or '').lstrip('#').lower()
    for i, lf in enumerate(loaded):
        if (lf.get('type') or '').lower() != req_type:
            continue
        if req_color and (lf.get('color') or '').lstrip('#').lower() != req_color:
            continue
        return i
    return None

def _multi_filament_mismatch(filament_map: list[dict], loaded_filaments: list[dict]) -> bool:
    """Return True if any catalog-assigned color has no matching loaded slot."""
    for entry in filament_map:
        if entry.get('filament_type') is None:
            continue  # slot assignment — no filament constraint
        matched = _find_slot_for_filament(
            entry['filament_type'], entry.get('filament_color'), loaded_filaments
        )
        if matched is None:
            return True
    return False
```

### Slot resolution before slicing

Before building `multi_presets`, resolve all catalog entries to `tool_index`:

```python
def _resolve_filament_map(filament_map: list[dict], loaded_filaments: list[dict]) -> list[dict]:
    resolved = []
    for entry in filament_map:
        if entry.get('tool_index') is not None:
            resolved.append(entry)
        else:
            slot_idx = _find_slot_for_filament(
                entry['filament_type'], entry.get('filament_color'), loaded_filaments
            )
            if slot_idx is None:
                raise ValueError(
                    f"Filament {entry['filament_type']} not loaded on printer — cannot slice"
                )
            resolved.append({**entry, 'tool_index': slot_idx})
    return resolved
```

The `ValueError` (or appropriate exception class used in `queue_engine.py`) triggers the existing `slice_failed` handling — the printer config is marked failed, the job requeues if other configs remain, and can be unblocked once the operator loads the required filament.

---

## When Spoolman is disabled

`spoolmanActive` is false → catalog `<optgroup>` is not rendered → the dropdown shows slots only. Behavior is identical to today. Existing `filamentMap` entries with `filament_id` set (from a time when Spoolman was active) are preserved but shown as slot entries using the first available slot as a fallback.

---

## Testing

### `PerPrinterConfig` unit tests

- Dropdown renders both `Slots` and `Catalog` optgroups when `spoolmanActive`.
- Selecting a catalog filament: `onChange` called with `filamentMap` entry `{ filament_id: 7, tool_index: null, filament_type: 'PLA', filament_color: '#5B9BD5' }`.
- Selecting a slot: entry is `{ tool_index: 1, filament_id: null, filament_type: null, filament_color: null }`.
- Green badge rendered when filament type matches a loaded slot.
- Amber badge rendered when filament type has no matching loaded slot.
- When `spoolmanActive` is false: no catalog optgroup; dropdown shows slots only.
- Existing `filamentMap` with `tool_index`-only entries renders correctly (backward compat).

### Queue engine unit tests

- `_multi_filament_mismatch` returns `False` when all `filament_id` entries have a matching loaded slot.
- `_multi_filament_mismatch` returns `True` when one entry has no match.
- `_resolve_filament_map` replaces `filament_id` entries with resolved `tool_index`.
- `_resolve_filament_map` raises `SliceError` when a `filament_id` can't be matched at slice time.
- Slot-only `filamentMap` entries pass through `_resolve_filament_map` unchanged.

### `NewJobScreen` integration tests

- Submitting a multi-filament job with a catalog assignment sends correct `filament_map` payload: `[{ model_filament: 1, tool_index: null, filament_id: 7, filament_type: 'PLA', filament_color: '#5B9BD5' }, ...]`.
