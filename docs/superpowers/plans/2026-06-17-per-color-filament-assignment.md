# Per-Color Filament Assignment for Multi-Material Jobs — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let operators assign each model color in a multi-material job to either a direct tool slot or a Spoolman catalog filament; queue engine resolves catalog filament → slot index at dispatch/slice time.

**Architecture:** Four sequential tasks. Task 1–2 extend the backend queue engine. Task 3 updates TypeScript types and the existing identity-map defaults (no visible behavior change). Task 4 replaces the per-row slot dropdown with a unified slot+catalog dropdown and adds preview badges.

**Tech Stack:** Python/FastAPI backend (`queue_engine.py`, `routes/jobs.py`), React/TypeScript frontend (`PerPrinterConfig.tsx`, `api/queue.ts`), Vitest.

---

## File map

| File | Change |
|---|---|
| `backend/app/services/queue_engine.py` | Add `_find_slot_for_filament`, update `_mapped_tools_loaded` + `_filament_mismatch`, add `_resolve_filament_map`, call it in `_run_slice_and_print` |
| `backend/tests/services/test_queue_filament_map.py` | Add tests for new helpers |
| `frontend/src/api/queue.ts` | Widen `filament_map` entry type on `PrinterConfigInput` and `ApiJobPrinterConfig` |
| `frontend/src/components/PerPrinterConfig.tsx` | Widen `PerPrinterCfg.filamentMap` type, add helpers, replace multi-filament per-row select |
| `frontend/src/components/PerPrinterConfig.test.tsx` | Update existing multi-material test, add catalog tests |

---

## Task 1: Queue engine — catalog-slot helpers and updated eligibility check

**Files:**
- Modify: `backend/app/services/queue_engine.py` (functions `_mapped_tools_loaded` lines 58–62 and `_filament_mismatch` lines 64–81)
- Test: `backend/tests/services/test_queue_filament_map.py`

- [ ] **Step 1: Write failing tests**

Append to `backend/tests/services/test_queue_filament_map.py`:

```python
from app.services.queue_engine import _find_slot_for_filament


LOADED_MIXED = [
    {"slot": 0, "type": "PLA",  "color": "#5B9BD5", "filament_profile": "PLA @ECC"},
    {"slot": 1, "type": "PETG", "color": "#FFFFFF",  "filament_profile": "PETG @ECC"},
]


# ── _find_slot_for_filament ──────────────────────────────────────────────────

def test_find_slot_type_match():
    assert _find_slot_for_filament("PLA", None, LOADED_MIXED) == 0

def test_find_slot_type_and_color_match():
    assert _find_slot_for_filament("PETG", "#FFFFFF", LOADED_MIXED) == 1

def test_find_slot_color_mismatch_returns_none():
    assert _find_slot_for_filament("PLA", "#000000", LOADED_MIXED) is None

def test_find_slot_type_not_loaded_returns_none():
    assert _find_slot_for_filament("ABS", None, LOADED_MIXED) is None

def test_find_slot_empty_loaded_returns_none():
    assert _find_slot_for_filament("PLA", None, []) is None

def test_find_slot_color_stripped_hash():
    # filament_color stored with #, loaded slot color also with # — both normalised
    assert _find_slot_for_filament("PETG", "FFFFFF", LOADED_MIXED) == 1


# ── _mapped_tools_loaded with catalog entries (no tool_index) ────────────────

def test_mapped_tools_loaded_skips_catalog_entries():
    # Catalog entry has tool_index=None — should be skipped, not cause KeyError
    mixed_map = [
        {"model_filament": 1, "tool_index": 0},
        {"model_filament": 2, "tool_index": None, "filament_type": "PETG"},
    ]
    assert _mapped_tools_loaded(mixed_map, LOADED_MIXED) is True


# ── _filament_mismatch with catalog entries ──────────────────────────────────

def test_filament_mismatch_catalog_entry_matched():
    cfg = _cfg(filament_map=[
        {"model_filament": 1, "tool_index": None, "filament_type": "PLA", "filament_color": None},
    ])
    assert _filament_mismatch(cfg, LOADED_MIXED) is None

def test_filament_mismatch_catalog_entry_not_loaded():
    cfg = _cfg(filament_map=[
        {"model_filament": 1, "tool_index": None, "filament_type": "ABS", "filament_color": None},
    ])
    assert _filament_mismatch(cfg, LOADED_MIXED) is not None

def test_filament_mismatch_mixed_map_slot_bad():
    # One slot entry out of range, one catalog entry matched — whole map fails
    cfg = _cfg(filament_map=[
        {"model_filament": 1, "tool_index": 99},
        {"model_filament": 2, "tool_index": None, "filament_type": "PETG", "filament_color": None},
    ])
    assert _filament_mismatch(cfg, LOADED_MIXED) is not None

def test_filament_mismatch_mixed_map_catalog_bad():
    # Slot entry valid, catalog entry unmatched — whole map fails
    cfg = _cfg(filament_map=[
        {"model_filament": 1, "tool_index": 0},
        {"model_filament": 2, "tool_index": None, "filament_type": "ABS", "filament_color": None},
    ])
    assert _filament_mismatch(cfg, LOADED_MIXED) is not None

def test_filament_mismatch_all_slot_entries_unchanged():
    # Backward compat: old-style slot-only map still works
    assert _filament_mismatch(
        _cfg(filament_map=[{"model_filament": 1, "tool_index": 0}]), LOADED_MIXED
    ) is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && python -m pytest tests/services/test_queue_filament_map.py -v 2>&1 | tail -20
```

Expected: multiple FAILED (ImportError on `_find_slot_for_filament`, KeyError on catalog entries).

- [ ] **Step 3: Add `_find_slot_for_filament`, update `_mapped_tools_loaded`, update `_filament_mismatch`**

In `backend/app/services/queue_engine.py`, replace lines 27–81 with:

```python
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm_color(value) -> str:
    return str(value or "").strip().lstrip("#").lower()


def _find_slot_for_filament(
    filament_type: str, filament_color: str | None, loaded: list
) -> int | None:
    """Return the index of the first loaded slot matching type (+color if given), or None."""
    req_type = filament_type.strip().lower()
    req_color = _norm_color(filament_color)
    for i, lf in enumerate(loaded or []):
        if (lf.get("type") or "").strip().lower() != req_type:
            continue
        if req_color and _norm_color(lf.get("color")) != req_color:
            continue
        return i
    return None


def _matching_loaded_filament(config: JobPrinterConfig, loaded: list) -> dict | None:
    """The printer's loaded filament slot that satisfies the job's ask (type AND
    color), or None. A job with no declared requirement matches the first slot.

    The OrcaSlicer filament *profile* used for slicing is a printer-level setting
    that lives on the matched slot (the "provide"); the job only declares the
    desired type/color (the "ask")."""
    req_type = (config.filament_type or "").strip().lower()
    req_color = _norm_color(config.filament_color)
    if not req_type and not req_color:
        return (loaded[0] if loaded else None)
    for f in loaded or []:
        if str(f.get("type", "")).strip().lower() == req_type and _norm_color(f.get("color")) == req_color:
            return f
    return None


def _slot_for_config(config, loaded: list) -> dict | None:
    """The loaded slot this config should print with: the explicit tool_index slot
    if set (multi-tool printers), else the type/color ask match."""
    ti = getattr(config, "tool_index", None)
    if ti is not None:
        loaded = loaded or []
        return loaded[ti] if 0 <= ti < len(loaded) else None
    return _matching_loaded_filament(config, loaded)


def _mapped_tools_loaded(filament_map: list, loaded: list) -> bool:
    """True if every slot-assigned entry's tool_index is within the loaded slots list.
    Catalog entries (tool_index is None) are skipped."""
    loaded = loaded or []
    return all(
        0 <= e["tool_index"] < len(loaded)
        for e in (filament_map or [])
        if e.get("tool_index") is not None
    )


def _filament_mismatch(config: JobPrinterConfig, loaded: list) -> str | None:
    """Return a reason string if the config can't be satisfied by the printer's
    loaded filaments, else None."""
    fmap = getattr(config, "filament_map", None)
    if fmap:
        if not _mapped_tools_loaded(fmap, loaded):
            return "a mapped tool has no loaded filament"
        for entry in fmap:
            ft = entry.get("filament_type")
            if ft is None:
                continue
            if _find_slot_for_filament(ft, entry.get("filament_color"), loaded or []) is None:
                return f"required filament {ft!r} not loaded"
        return None
    if getattr(config, "tool_index", None) is not None:
        if _slot_for_config(config, loaded) is None:
            return f"tool T{config.tool_index} has no loaded filament"
        return None
    req_type = (config.filament_type or "").strip().lower()
    req_color = _norm_color(config.filament_color)
    if not req_type and not req_color:
        return None
    if _matching_loaded_filament(config, loaded) is not None:
        return None
    return (f"loaded filament doesn't match required "
            f"{config.filament_type or '?'} {config.filament_color or '?'}")
```

- [ ] **Step 4: Run all queue filament map tests to verify they pass**

```bash
cd backend && python -m pytest tests/services/test_queue_filament_map.py -v 2>&1 | tail -20
```

Expected: all PASSED.

- [ ] **Step 5: Run full backend test suite to verify no regressions**

```bash
cd backend && python -m pytest -v 2>&1 | tail -10
```

Expected: all PASSED.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/queue_engine.py backend/tests/services/test_queue_filament_map.py
git commit -m "feat(queue): catalog-filament eligibility check for multi-material jobs"
```

---

## Task 2: Queue engine — `_resolve_filament_map` and integration in `_run_slice_and_print`

**Files:**
- Modify: `backend/app/services/queue_engine.py` (add `_resolve_filament_map` near line 82; update `_run_slice_and_print` near line 267)
- Test: `backend/tests/services/test_queue_filament_map.py`

- [ ] **Step 1: Write failing tests**

Append to `backend/tests/services/test_queue_filament_map.py`:

```python
from app.services.queue_engine import _resolve_filament_map


def test_resolve_slot_entries_unchanged():
    fm = [{"model_filament": 1, "tool_index": 0, "filament_id": None, "filament_type": None, "filament_color": None}]
    result = _resolve_filament_map(fm, LOADED_MIXED)
    assert result[0]["tool_index"] == 0

def test_resolve_catalog_entry_found():
    fm = [{"model_filament": 1, "tool_index": None, "filament_id": 7, "filament_type": "PLA", "filament_color": None}]
    result = _resolve_filament_map(fm, LOADED_MIXED)
    assert result[0]["tool_index"] == 0

def test_resolve_catalog_entry_with_color():
    fm = [{"model_filament": 1, "tool_index": None, "filament_id": 19, "filament_type": "PETG", "filament_color": "#FFFFFF"}]
    result = _resolve_filament_map(fm, LOADED_MIXED)
    assert result[0]["tool_index"] == 1

def test_resolve_catalog_entry_not_loaded_raises():
    fm = [{"model_filament": 1, "tool_index": None, "filament_id": 5, "filament_type": "ABS", "filament_color": None}]
    try:
        _resolve_filament_map(fm, LOADED_MIXED)
        assert False, "should have raised"
    except ValueError as exc:
        assert "ABS" in str(exc)

def test_resolve_mixed_map():
    fm = [
        {"model_filament": 1, "tool_index": 0, "filament_id": None, "filament_type": None, "filament_color": None},
        {"model_filament": 2, "tool_index": None, "filament_id": 19, "filament_type": "PETG", "filament_color": "#FFFFFF"},
    ]
    result = _resolve_filament_map(fm, LOADED_MIXED)
    assert result[0]["tool_index"] == 0
    assert result[1]["tool_index"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && python -m pytest tests/services/test_queue_filament_map.py::test_resolve_slot_entries_unchanged -v 2>&1 | tail -5
```

Expected: FAILED with ImportError on `_resolve_filament_map`.

- [ ] **Step 3: Add `_resolve_filament_map` to `queue_engine.py`**

After the `_filament_mismatch` function (around line 82), add:

```python
def _resolve_filament_map(filament_map: list, loaded: list) -> list:
    """Resolve any catalog-assigned entries (filament_type set, tool_index None)
    to their matching loaded slot index. Returns a new list with all entries
    having tool_index set. Raises ValueError if any catalog entry has no match."""
    resolved = []
    for entry in filament_map:
        if entry.get("tool_index") is not None:
            resolved.append(entry)
        else:
            ft = entry.get("filament_type")
            fc = entry.get("filament_color")
            slot_idx = _find_slot_for_filament(ft or "", fc, loaded or [])
            if slot_idx is None:
                raise ValueError(
                    f"Filament {ft!r} not loaded on printer — cannot slice"
                )
            resolved.append({**entry, "tool_index": slot_idx})
    return resolved
```

- [ ] **Step 4: Integrate `_resolve_filament_map` into `_run_slice_and_print`**

In `backend/app/services/queue_engine.py`, find the block that builds `prepare_hook` (around line 266):

```python
        prepare_hook = None
        if client is not None and (cfg_tool_index is not None or cfg_filament_map):
            prepare_hook = (lambda p, c=client, ti=cfg_tool_index, fm=cfg_filament_map:
                            c.remap_sliceable_3mf(p, tool_index=ti, filament_map=fm))
```

Replace with:

```python
        # Resolve any catalog filament entries to slot indices before slicing.
        if cfg_filament_map:
            try:
                cfg_filament_map = _resolve_filament_map(cfg_filament_map, loaded)
            except ValueError as exc:
                await self._handle_slice_failure(job_id, printer_id, str(exc))
                return

        prepare_hook = None
        if client is not None and (cfg_tool_index is not None or cfg_filament_map):
            prepare_hook = (lambda p, c=client, ti=cfg_tool_index, fm=cfg_filament_map:
                            c.remap_sliceable_3mf(p, tool_index=ti, filament_map=fm))
```

- [ ] **Step 5: Run queue filament map tests**

```bash
cd backend && python -m pytest tests/services/test_queue_filament_map.py -v 2>&1 | tail -20
```

Expected: all PASSED.

- [ ] **Step 6: Run full backend suite**

```bash
cd backend && python -m pytest -v 2>&1 | tail -10
```

Expected: all PASSED.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/queue_engine.py backend/tests/services/test_queue_filament_map.py
git commit -m "feat(queue): resolve catalog filament entries to slot index before slicing"
```

---

## Task 3: Frontend — widen `filamentMap` entry type and update identity-map defaults

**Files:**
- Modify: `frontend/src/api/queue.ts` (lines 36, 70)
- Modify: `frontend/src/components/PerPrinterConfig.tsx` (line 14, identity map around line 166, `handleMapChange` around line 173)
- Modify: `frontend/src/components/PerPrinterConfig.test.tsx` (update existing multi-material onChange assertion)

- [ ] **Step 1: Update `filament_map` type in `frontend/src/api/queue.ts`**

Line 36 — change:
```ts
  filament_map?: { model_filament: number; tool_index: number }[] | null;
```
to:
```ts
  filament_map?: { model_filament: number; tool_index: number | null; filament_id: number | null; filament_type: string | null; filament_color: string | null }[] | null;
```

Line 70 — change `ApiJobPrinterConfig.filament_map`:
```ts
  filament_map?: { model_filament: number; tool_index: number }[] | null;
```
to:
```ts
  filament_map?: { model_filament: number; tool_index: number | null; filament_id: number | null; filament_type: string | null; filament_color: string | null }[] | null;
```

- [ ] **Step 2: Update `PerPrinterCfg.filamentMap` type in `frontend/src/components/PerPrinterConfig.tsx`**

Line 14 — change:
```ts
  filamentMap: { model_filament: number; tool_index: number }[] | null;
```
to:
```ts
  filamentMap: { model_filament: number; tool_index: number | null; filament_id: number | null; filament_type: string | null; filament_color: string | null }[] | null;
```

- [ ] **Step 3: Update identity-map default and `handleMapChange` in `PerPrinterConfig.tsx`**

Find the identity-map default (around line 166):
```ts
                  modelFilaments.map(mf => ({
                    model_filament: mf.index,
                    tool_index: Math.min(mf.index - 1, slots.length - 1),
                  }));
```
(appears twice — both in the `const currentMap` expression and inside `handleMapChange`'s `base`). Replace both occurrences with:
```ts
                  modelFilaments.map(mf => ({
                    model_filament: mf.index,
                    tool_index: Math.min(mf.index - 1, slots.length - 1),
                    filament_id: null,
                    filament_type: null,
                    filament_color: null,
                  }));
```

Find the `newMap.push` inside `handleMapChange` (around line 181):
```ts
                  newMap.push({ model_filament: f.index, tool_index: chosenTool });
```
Replace with:
```ts
                  newMap.push({ model_filament: f.index, tool_index: chosenTool, filament_id: null, filament_type: null, filament_color: null });
```

- [ ] **Step 4: Update the existing multi-material onChange test in `PerPrinterConfig.test.tsx`**

Find the test `'multi-material: changing map-tool-2 calls onChange with filamentMap containing...'` (line 93). Change the assertion from:
```ts
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({
        filamentMap: expect.arrayContaining([
          { model_filament: 2, tool_index: 2 },
        ]),
      }),
    );
```
to:
```ts
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({
        filamentMap: expect.arrayContaining([
          expect.objectContaining({ model_filament: 2, tool_index: 2, filament_id: null }),
        ]),
      }),
    );
```

- [ ] **Step 5: Run frontend tests to verify no regressions**

```bash
cd frontend && npx vitest run src/components/PerPrinterConfig.test.tsx 2>&1 | tail -15
```

Expected: all PASSED.

- [ ] **Step 6: Verify TypeScript compiles cleanly**

```bash
cd frontend && npm run build 2>&1 | tail -10
```

Expected: build succeeds with no type errors.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/api/queue.ts frontend/src/components/PerPrinterConfig.tsx frontend/src/components/PerPrinterConfig.test.tsx
git commit -m "feat(types): widen filamentMap entry to support catalog filament assignment"
```

---

## Task 4: Frontend — unified dropdown + preview badges

**Files:**
- Modify: `frontend/src/components/PerPrinterConfig.tsx` (multi-filament mapping section, lines 158–210)
- Modify: `frontend/src/components/PerPrinterConfig.test.tsx` (update `value: '2'` → `value: 't:2'`, add catalog tests)

- [ ] **Step 1: Add `parseOrcaProfiles` to the spoolman mock in `PerPrinterConfig.test.tsx`**

Find the `vi.mock('../api/spoolman', ...)` block (lines 6–11). Add `parseOrcaProfiles` to the factory so it doesn't throw when the catalog path is tested with `spoolmanActive`:

```ts
vi.mock('../api/spoolman', () => ({
  useSpoolmanConfig: vi.fn().mockReturnValue({ config: null, refetch: vi.fn() }),
  useFilaments: vi.fn().mockReturnValue([]),
  filamentDisplayName: vi.fn((f: { vendor?: { name: string }; name: string }) =>
    f.vendor ? `${f.vendor.name} ${f.name}` : f.name),
  parseOrcaProfiles: vi.fn(() => ({})),
}));
```

- [ ] **Step 2: Write failing tests for the unified dropdown and preview badges**

Append to `frontend/src/components/PerPrinterConfig.test.tsx`:

```ts
import { vi, describe, it, expect, beforeEach } from 'vitest';
import * as spoolman from '../api/spoolman';

const MOCK_FILAMENTS = [
  { id: 7,  name: 'Sky Blue', vendor: { id: 2, name: 'ELEGOO' }, material: 'PLA',  color_hex: '5B9BD5' },
  { id: 19, name: 'White',    vendor: { id: 3, name: 'Sunlu'  }, material: 'PETG', color_hex: 'FFFFFF' },
];

function mockSpoolman(enabled: boolean) {
  vi.mocked(spoolman.useSpoolmanConfig).mockReturnValue(
    enabled
      ? { config: { enabled: true, url: 'http://artemis:7912', api_key: null }, refetch: vi.fn() }
      : { config: null, refetch: vi.fn() },
  );
  vi.mocked(spoolman.useFilaments).mockReturnValue(enabled ? MOCK_FILAMENTS as never : []);
}

describe('PerPrinterConfig — multi-material unified dropdown', () => {
  beforeEach(() => vi.clearAllMocks());

  it('slot-only: renders optgroup "Slots" but no "Catalog" when spoolman off', async () => {
    mockSpoolman(false);
    renderCfg(MULTI, defaultPerPrinterCfg(), MODEL_FILAMENTS_3);
    const sel1 = await screen.findByTestId('map-tool-1');
    const html = sel1.innerHTML;
    expect(html).toContain('Slots');
    expect(html).not.toContain('Catalog');
  });

  it('renders "Catalog" optgroup when spoolman is on', async () => {
    mockSpoolman(true);
    renderCfg(MULTI, defaultPerPrinterCfg(), MODEL_FILAMENTS_3);
    const sel1 = await screen.findByTestId('map-tool-1');
    expect(sel1.innerHTML).toContain('Catalog');
  });

  it('catalog optgroup contains filament options', async () => {
    mockSpoolman(true);
    renderCfg(MULTI, defaultPerPrinterCfg(), MODEL_FILAMENTS_3);
    const sel1 = await screen.findByTestId('map-tool-1');
    const options = Array.from(sel1.querySelectorAll('option')).map(o => o.value);
    expect(options).toContain('f:7');
    expect(options).toContain('f:19');
  });

  it('selecting a catalog filament calls onChange with filament_id, filament_type, filament_color', async () => {
    mockSpoolman(true);
    const onChange = renderCfg(MULTI, defaultPerPrinterCfg(), MODEL_FILAMENTS_3);
    const sel1 = await screen.findByTestId('map-tool-1');
    fireEvent.change(sel1, { target: { value: 'f:7' } });
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({
        filamentMap: expect.arrayContaining([
          expect.objectContaining({
            model_filament: 1,
            tool_index: null,
            filament_id: 7,
            filament_type: 'PLA',
            filament_color: '#5B9BD5',
          }),
        ]),
      }),
    );
  });

  it('selecting a slot calls onChange with tool_index set and filament fields null', async () => {
    mockSpoolman(true);
    const onChange = renderCfg(MULTI, defaultPerPrinterCfg(), MODEL_FILAMENTS_3);
    const sel2 = await screen.findByTestId('map-tool-2');
    fireEvent.change(sel2, { target: { value: 't:1' } });
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({
        filamentMap: expect.arrayContaining([
          expect.objectContaining({
            model_filament: 2,
            tool_index: 1,
            filament_id: null,
            filament_type: null,
          }),
        ]),
      }),
    );
  });

  it('green badge shown when filament_type matches a loaded slot', async () => {
    mockSpoolman(true);
    const cfg = {
      ...defaultPerPrinterCfg(),
      filamentMap: [
        { model_filament: 1, tool_index: null, filament_id: 7, filament_type: 'PLA', filament_color: '#5B9BD5' },
        { model_filament: 2, tool_index: 0, filament_id: null, filament_type: null, filament_color: null },
        { model_filament: 3, tool_index: 2, filament_id: null, filament_type: null, filament_color: null },
      ],
    };
    // MULTI has slot 0 = PLA #fff, slot 1 = PETG #000, slot 2 = TPU #0f0
    // F1 asks for PLA #5B9BD5 — color doesn't match any slot, so amber badge
    // Let's use a color that matches slot 0
    const cfgMatch = {
      ...defaultPerPrinterCfg(),
      filamentMap: [
        { model_filament: 1, tool_index: null, filament_id: 7, filament_type: 'PLA', filament_color: '#fff' },
        { model_filament: 2, tool_index: 0, filament_id: null, filament_type: null, filament_color: null },
        { model_filament: 3, tool_index: 2, filament_id: null, filament_type: null, filament_color: null },
      ],
    };
    renderCfg(MULTI, cfgMatch as any, MODEL_FILAMENTS_3);
    // Green badge text contains "loaded now"
    const badge = await screen.findByText(/loaded now/i);
    expect(badge).toBeTruthy();
  });

  it('amber badge shown when filament_type has no matching loaded slot', async () => {
    mockSpoolman(true);
    const cfgNoMatch = {
      ...defaultPerPrinterCfg(),
      filamentMap: [
        { model_filament: 1, tool_index: null, filament_id: 5, filament_type: 'ABS', filament_color: null },
        { model_filament: 2, tool_index: 0, filament_id: null, filament_type: null, filament_color: null },
        { model_filament: 3, tool_index: 2, filament_id: null, filament_type: null, filament_color: null },
      ],
    };
    renderCfg(MULTI, cfgNoMatch as any, MODEL_FILAMENTS_3);
    const badge = await screen.findByText(/will block at slice/i);
    expect(badge).toBeTruthy();
  });
});
```

- [ ] **Step 3: Run failing tests to verify they fail**

```bash
cd frontend && npx vitest run src/components/PerPrinterConfig.test.tsx 2>&1 | tail -20
```

Expected: the new catalog tests FAIL (no `Catalog` optgroup yet, no badges yet).

- [ ] **Step 4: Implement helpers in `PerPrinterConfig.tsx`**

After the `FILAMENT_TYPES` constant (around line 28), add:

```ts
type FilamentMapEntry = {
  model_filament: number;
  tool_index: number | null;
  filament_id: number | null;
  filament_type: string | null;
  filament_color: string | null;
};

function encodeAssignment(entry: FilamentMapEntry): string {
  if (entry.tool_index !== null) return `t:${entry.tool_index}`;
  if (entry.filament_id !== null) return `f:${entry.filament_id}`;
  return 't:0';
}

function findLoadedSlotForEntry(
  entry: { filament_type: string | null; filament_color: string | null },
  slots: { type?: string; color?: string }[],
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

- [ ] **Step 5: Replace the multi-filament mapping section in `PerPrinterConfig.tsx`**

Find the block starting at line 158 (`(modelFilaments && modelFilaments.length > 1 && slots.length >= 1) ? (`). Replace the entire inner `<div>` content (the filament mapping section — from `<label className="label">Filament mapping</label>` through the closing `</div>` of the mapping section) with:

```tsx
          <div>
            <label className="label">Filament mapping</label>
            <div className="col gap-2" style={{ marginTop: 4 }}>
              {modelFilaments.map(f => {
                const currentMap: FilamentMapEntry[] =
                  (config.filamentMap as FilamentMapEntry[] | null) ??
                  modelFilaments!.map(mf => ({
                    model_filament: mf.index,
                    tool_index: Math.min(mf.index - 1, slots.length - 1),
                    filament_id: null,
                    filament_type: null,
                    filament_color: null,
                  }));
                const entry: FilamentMapEntry = currentMap.find(e => e.model_filament === f.index) ?? {
                  model_filament: f.index,
                  tool_index: Math.min(f.index - 1, slots.length - 1),
                  filament_id: null,
                  filament_type: null,
                  filament_color: null,
                };

                function handleAssignmentChange(val: string) {
                  const base: FilamentMapEntry[] =
                    (config.filamentMap as FilamentMapEntry[] | null) ??
                    modelFilaments!.map(mf => ({
                      model_filament: mf.index,
                      tool_index: Math.min(mf.index - 1, slots.length - 1),
                      filament_id: null,
                      filament_type: null,
                      filament_color: null,
                    }));
                  const newMap = base.filter(e => e.model_filament !== f.index);
                  if (val.startsWith('t:')) {
                    newMap.push({ model_filament: f.index, tool_index: Number(val.slice(2)), filament_id: null, filament_type: null, filament_color: null });
                  } else {
                    const fid = Number(val.slice(2));
                    const fil = filaments.find(fil => fil.id === fid)!;
                    newMap.push({
                      model_filament: f.index,
                      tool_index: null,
                      filament_id: fid,
                      filament_type: fil.material,
                      filament_color: fil.color_hex ? `#${fil.color_hex}` : null,
                    });
                  }
                  newMap.sort((a, b) => a.model_filament - b.model_filament);
                  onChange({ filamentMap: newMap });
                }

                const matchedSlot = entry.filament_id !== null
                  ? findLoadedSlotForEntry(entry, slots)
                  : null;

                return (
                  <div key={f.index}>
                    <div className="row gap-2" style={{ alignItems: 'center' }}>
                      <div style={{
                        width: 14, height: 14, borderRadius: 3, flexShrink: 0,
                        background: f.color || '#888', border: '1px solid var(--border-2)',
                      }} />
                      <span className="tiny" style={{ flex: 1, minWidth: 0, color: 'var(--text-2)' }}>
                        Filament {f.index}{f.type ? ` · ${f.type}` : ''}
                      </span>
                      <select
                        data-testid={`map-tool-${f.index}`}
                        className="select"
                        style={{ flex: '0 0 auto', minWidth: 160 }}
                        value={encodeAssignment(entry)}
                        onChange={e => handleAssignmentChange(e.target.value)}
                      >
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
                    </div>
                    {entry.filament_id !== null && (
                      <div style={{ marginLeft: 22, marginTop: 2 }}>
                        {matchedSlot !== null ? (
                          <span style={{
                            display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 10,
                            padding: '2px 6px', borderRadius: 4,
                            background: 'rgba(34,197,94,0.10)', border: '1px solid rgba(34,197,94,0.30)',
                            color: 'var(--ok)',
                          }}>
                            ✓ T{matchedSlot} loaded now
                          </span>
                        ) : (
                          <span style={{
                            display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 10,
                            padding: '2px 6px', borderRadius: 4,
                            background: 'rgba(251,191,36,0.10)', border: '1px solid rgba(251,191,36,0.30)',
                            color: 'var(--warn)',
                          }}>
                            ⚠ {entry.filament_type || '?'} not loaded — will block at slice
                          </span>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
```

- [ ] **Step 6: Update the existing multi-material slot-change test to use the new encoding**

In `PerPrinterConfig.test.tsx`, find the test `'multi-material: changing map-tool-2 calls onChange with filamentMap containing...'`. Change the fireEvent line from:
```ts
    fireEvent.change(sel2, { target: { value: '2' } });
```
to:
```ts
    fireEvent.change(sel2, { target: { value: 't:2' } });
```

- [ ] **Step 7: Run all PerPrinterConfig tests**

```bash
cd frontend && npx vitest run src/components/PerPrinterConfig.test.tsx 2>&1 | tail -20
```

Expected: all PASSED.

- [ ] **Step 8: Run full frontend test suite**

```bash
cd frontend && npx vitest run 2>&1 | tail -10
```

Expected: all PASSED.

- [ ] **Step 9: Verify TypeScript**

```bash
cd frontend && npm run build 2>&1 | tail -10
```

Expected: build succeeds.

- [ ] **Step 10: Commit**

```bash
git add frontend/src/components/PerPrinterConfig.tsx frontend/src/components/PerPrinterConfig.test.tsx
git commit -m "feat(ui): unified slot+catalog dropdown with preview badges for multi-material jobs"
```
