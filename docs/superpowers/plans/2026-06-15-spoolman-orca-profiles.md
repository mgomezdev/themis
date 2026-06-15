# Spoolman `orca_profiles` — Filament Profile Auto-Mapping Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow operators to configure, per Spoolman filament × OrcaSlicer machine preset, which filament preset(s) to use — stored as a JSON string in Spoolman's `orca_profiles` custom field — and auto-apply that mapping when selecting filaments in job creation and Fleet slot management.

**Architecture:** Read path is the existing raw Spoolman passthrough (no backend changes). Write path uses a new backend proxy endpoint `PATCH /api/v1/spoolman/filaments/{id}` to avoid exposing the Spoolman API key. Resolution logic is entirely frontend: `parseOrcaProfiles(filament)` decodes the JSON string and the appropriate component applies auto-select / restrict / fallback rules.

**Tech Stack:** FastAPI + httpx (backend proxy), React/TypeScript (frontend), SQLite (Spoolman config row for URL/api_key)

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `backend/app/services/spoolman_service.py` | Modify | Add `patch_filament()` — GET current filament, merge `orca_profiles`, PATCH back |
| `backend/app/api/routes/spoolman.py` | Modify | Add `PATCH /filaments/{filament_id}` endpoint |
| `backend/tests/api/test_spoolman_patch.py` | Create | Tests for the patch endpoint |
| `frontend/src/api/spoolman.ts` | Modify | Add `extra` to `ApiFilament`, add `parseOrcaProfiles`, add `patchFilamentOrcaProfiles` |
| `frontend/src/components/FilamentProfileSelect.tsx` | Create | Searchable single-select for OrcaSlicer filament preset names |
| `frontend/src/components/FilamentProfileMultiSelect.tsx` | Create | Typeahead multi-select with chips for filament preset names |
| `frontend/src/screens/SpoolmanMappingsPage.tsx` | Create | Full Spoolman Mappings settings page |
| `frontend/src/screens/SettingsScreen.tsx` | Modify | Register `spoolman-mappings` page under Integrations nav |
| `frontend/src/components/PerPrinterConfig.tsx` | Modify | Add mapping-aware filament profile section |
| `frontend/src/screens/FleetScreen.tsx` | Modify | Make `FilamentPicker` mapping-aware |

---

### Task 1: Backend — Spoolman patch service + endpoint + tests

**Files:**
- Modify: `backend/app/services/spoolman_service.py`
- Modify: `backend/app/api/routes/spoolman.py`
- Create: `backend/tests/api/test_spoolman_patch.py`

- [ ] **Step 1: Write failing tests**

Create `backend/tests/api/test_spoolman_patch.py`:

```python
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient


async def _seed_spoolman(client: AsyncClient) -> None:
    await client.put(
        "/api/v1/settings/spoolman",
        json={"enabled": True, "url": "http://spoolman.test", "api_key": None},
    )


async def test_patch_filament_returns_updated_filament(client: AsyncClient):
    await _seed_spoolman(client)
    updated = {"id": 42, "name": "PLA Basic", "extra": {"orca_profiles": '{"P1S": ["Bambu PLA @P1S"]}'}}

    with patch(
        "app.api.routes.spoolman.spoolman_service.patch_filament",
        new_callable=AsyncMock,
        return_value=updated,
    ) as mock_patch:
        resp = await client.patch(
            "/api/v1/spoolman/filaments/42",
            json={"orca_profiles": {"P1S": ["Bambu PLA @P1S"]}},
        )

    assert resp.status_code == 200
    assert resp.json() == updated
    mock_patch.assert_called_once_with(
        "http://spoolman.test", None, 42, {"P1S": ["Bambu PLA @P1S"]}
    )


async def test_patch_filament_503_when_spoolman_not_configured(client: AsyncClient):
    resp = await client.patch(
        "/api/v1/spoolman/filaments/42",
        json={"orca_profiles": {}},
    )
    assert resp.status_code == 503


async def test_patch_filament_forwards_spoolman_error(client: AsyncClient):
    await _seed_spoolman(client)
    with patch(
        "app.api.routes.spoolman.spoolman_service.patch_filament",
        new_callable=AsyncMock,
        side_effect=Exception("Connection refused"),
    ):
        resp = await client.patch(
            "/api/v1/spoolman/filaments/99",
            json={"orca_profiles": {}},
        )
    assert resp.status_code == 503
    assert "Connection refused" in resp.json()["detail"]
```

- [ ] **Step 2: Run tests to confirm they fail**

```
cd backend
pytest tests/api/test_spoolman_patch.py -v
```

Expected: 3 FAILs (404 / AttributeError — endpoint doesn't exist yet)

- [ ] **Step 3: Add `patch_filament` to `spoolman_service.py`**

Open `backend/app/services/spoolman_service.py`. Add at the end (after `fetch_spools`):

```python
import json as _json


async def patch_filament(
    url: str, api_key: Optional[str], filament_id: int, orca_profiles: dict
) -> dict:
    """Merge orca_profiles into filament's extra field and PATCH Spoolman."""
    headers = _headers(api_key)
    base = url.rstrip("/")
    async with httpx.AsyncClient(timeout=10) as client:
        get_resp = await client.get(f"{base}/api/v1/filament/{filament_id}", headers=headers)
        get_resp.raise_for_status()
        existing_extra: dict = get_resp.json().get("extra") or {}

        merged_extra = {**existing_extra, "orca_profiles": _json.dumps(orca_profiles)}

        patch_resp = await client.patch(
            f"{base}/api/v1/filament/{filament_id}",
            json={"extra": merged_extra},
            headers=headers,
        )
        patch_resp.raise_for_status()
        return patch_resp.json()
```

- [ ] **Step 4: Add `PATCH /filaments/{filament_id}` to `spoolman.py`**

Open `backend/app/api/routes/spoolman.py`. Add after the `get_spools` route:

```python
from pydantic import BaseModel


class FilamentPatchBody(BaseModel):
    orca_profiles: dict


@router.patch("/filaments/{filament_id}")
async def patch_filament(
    filament_id: int,
    body: FilamentPatchBody,
    session: AsyncSession = Depends(get_session),
):
    row = await _config_or_503(session)
    try:
        return await spoolman_service.patch_filament(
            row.url, row.api_key, filament_id, body.orca_profiles
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc))
```

- [ ] **Step 5: Run tests to confirm they pass**

```
cd backend
pytest tests/api/test_spoolman_patch.py -v
```

Expected: 3 PASSes

- [ ] **Step 6: Fix filament profile resolution order in `queue_engine.py` and `jobs.py`**

The spec requires job-level `filament_profile` to override the printer slot's profile. Both files currently do `slot first`, which must be flipped.

In `backend/app/services/queue_engine.py` around line 243, replace:
```python
filament_profile = (slot or {}).get("filament_profile") or (config.filament_profile if config else None) or None
```
With:
```python
filament_profile = (config.filament_profile if config else None) or (slot or {}).get("filament_profile") or None
```

In `backend/app/api/routes/jobs.py` in `verify_slice` (around line 453), replace:
```python
filament_profile = (slot or {}).get("filament_profile") or config.filament_profile or None
```
With:
```python
filament_profile = config.filament_profile or (slot or {}).get("filament_profile") or None
```

- [ ] **Step 7: Run full backend suite to check for regressions**

```
cd backend
pytest -v
```

Expected: all green

- [ ] **Step 8: Commit**

```
git add backend/app/services/spoolman_service.py backend/app/api/routes/spoolman.py backend/tests/api/test_spoolman_patch.py backend/app/services/queue_engine.py
git commit -m "feat(spoolman): PATCH proxy endpoint + job-level filament profile overrides slot default"
```

---

### Task 2: Frontend API — extend types and add patch function

**Files:**
- Modify: `frontend/src/api/spoolman.ts`

- [ ] **Step 1: Add `extra` to `ApiFilament` and new helpers**

Open `frontend/src/api/spoolman.ts`. Make these changes:

**Replace the `ApiFilament` interface** (currently lines 3–11):

```typescript
export interface ApiFilament {
  id: number;
  name: string;
  vendor?: { id: number; name: string };
  material: string;
  color_hex?: string;
  settings_extruder_temp?: number;
  settings_bed_temp?: number;
  extra?: Record<string, unknown>;
}
```

**Add after `filamentDisplayName`** (after line 13):

```typescript
export function parseOrcaProfiles(f: ApiFilament): Record<string, string[]> {
  try {
    const raw = f.extra?.orca_profiles;
    if (!raw) return {};
    const parsed: unknown = typeof raw === 'string' ? JSON.parse(raw) : raw;
    if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) return {};
    return parsed as Record<string, string[]>;
  } catch {
    console.warn('[Themis] orca_profiles parse error for filament', f.id);
    return {};
  }
}

export async function patchFilamentOrcaProfiles(
  filamentId: number,
  orcaProfiles: Record<string, string[]>,
): Promise<unknown> {
  return request('/api/v1/spoolman/filaments/' + filamentId, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ orca_profiles: orcaProfiles }),
  });
}
```

- [ ] **Step 2: Verify TypeScript compiles**

```
cd frontend
npm run build 2>&1 | head -30
```

Expected: no TypeScript errors related to the changed file

- [ ] **Step 3: Commit**

```
git add frontend/src/api/spoolman.ts
git commit -m "feat(spoolman): add extra field to ApiFilament + parseOrcaProfiles helper"
```

---

### Task 3: `FilamentProfileSelect` — searchable single-select component

**Files:**
- Create: `frontend/src/components/FilamentProfileSelect.tsx`

- [ ] **Step 1: Create the component**

Create `frontend/src/components/FilamentProfileSelect.tsx`:

```typescript
import { useMemo, useState } from 'react';

export function FilamentProfileSelect({ profiles, value, onChange, placeholder = '— use printer default —' }: {
  profiles: string[];
  value: string | null;
  onChange: (v: string | null) => void;
  placeholder?: string;
}) {
  const [query, setQuery] = useState('');
  const showSearch = profiles.length > 12;

  const filtered = useMemo(() => {
    if (!query) return profiles;
    const q = query.toLowerCase();
    return profiles.filter(p => p.toLowerCase().includes(q));
  }, [profiles, query]);

  return (
    <div className="col gap-1">
      {showSearch && (
        <input
          className="input"
          placeholder="Search profiles…"
          value={query}
          onChange={e => setQuery(e.target.value)}
          style={{ fontSize: 12 }}
        />
      )}
      <select
        className="select"
        value={value ?? ''}
        onChange={e => onChange(e.target.value || null)}
      >
        <option value="">{placeholder}</option>
        {filtered.map(p => <option key={p} value={p}>{p}</option>)}
        {showSearch && query && filtered.length === 0 && (
          <option disabled>No matches</option>
        )}
      </select>
    </div>
  );
}
```

- [ ] **Step 2: Verify TypeScript compiles**

```
cd frontend
npm run build 2>&1 | head -30
```

Expected: no errors

- [ ] **Step 3: Commit**

```
git add frontend/src/components/FilamentProfileSelect.tsx
git commit -m "feat: FilamentProfileSelect — searchable single-select for OrcaSlicer preset names"
```

---

### Task 4: `FilamentProfileMultiSelect` — typeahead multi-select with chips

**Files:**
- Create: `frontend/src/components/FilamentProfileMultiSelect.tsx`

- [ ] **Step 1: Create the component**

Create `frontend/src/components/FilamentProfileMultiSelect.tsx`:

```typescript
import { useMemo, useRef, useState } from 'react';

export function FilamentProfileMultiSelect({ profiles, selected, onChange, placeholder = 'Search profiles…', emptyText }: {
  profiles: string[];
  selected: string[];
  onChange: (selected: string[]) => void;
  placeholder?: string;
  emptyText?: string;
}) {
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const filtered = useMemo(() => {
    const q = query.toLowerCase();
    return profiles.filter(p => !selected.includes(p) && p.toLowerCase().includes(q));
  }, [profiles, selected, query]);

  function add(p: string) {
    onChange([...selected, p]);
    setQuery('');
    inputRef.current?.focus();
  }

  function remove(p: string) {
    onChange(selected.filter(s => s !== p));
  }

  return (
    <div className="col gap-1">
      {selected.length > 0 && (
        <div className="row gap-1" style={{ flexWrap: 'wrap', marginBottom: 4 }}>
          {selected.map(p => (
            <span key={p} style={{
              display: 'inline-flex', alignItems: 'center', gap: 4,
              padding: '2px 8px 2px 10px', borderRadius: 999, fontSize: 11,
              background: 'var(--accent-glow)', border: '1px solid var(--accent-lo)',
              color: 'var(--accent-hi)', fontWeight: 500,
            }}>
              {p}
              <button
                onClick={() => remove(p)}
                style={{
                  background: 'none', border: 'none', cursor: 'pointer',
                  color: 'var(--accent-hi)', padding: 0, lineHeight: 1, fontSize: 13,
                  display: 'flex', alignItems: 'center',
                }}
                title={`Remove ${p}`}
              >×</button>
            </span>
          ))}
        </div>
      )}
      <div style={{ position: 'relative' }}>
        <input
          ref={inputRef}
          className="input"
          value={query}
          placeholder={selected.length > 0 ? 'Add another…' : placeholder}
          onChange={e => { setQuery(e.target.value); setOpen(true); }}
          onFocus={() => setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 150)}
          style={{ fontSize: 12 }}
        />
        {open && (filtered.length > 0 || (query && emptyText)) && (
          <div style={{
            position: 'absolute', top: '100%', left: 0, right: 0, zIndex: 50,
            background: 'var(--bg-2)', border: '1px solid var(--border-2)',
            borderRadius: 8, boxShadow: '0 8px 24px rgba(0,0,0,0.3)',
            maxHeight: 200, overflowY: 'auto', marginTop: 2,
          }}>
            {filtered.length > 0 ? filtered.map(p => (
              <div
                key={p}
                onMouseDown={() => add(p)}
                style={{
                  padding: '7px 12px', fontSize: 12, cursor: 'pointer',
                  color: 'var(--text-1)', borderBottom: '1px solid var(--border-1)',
                }}
                onMouseEnter={e => (e.currentTarget.style.background = 'var(--bg-3)')}
                onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
              >{p}</div>
            )) : (
              <div style={{ padding: '7px 12px', fontSize: 12, color: 'var(--text-3)' }}>
                {emptyText ?? 'No compatible profiles found'}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Verify TypeScript compiles**

```
cd frontend
npm run build 2>&1 | head -30
```

Expected: no errors

- [ ] **Step 3: Commit**

```
git add frontend/src/components/FilamentProfileMultiSelect.tsx
git commit -m "feat: FilamentProfileMultiSelect — typeahead chip-select for OrcaSlicer preset names"
```

---

### Task 5: Spoolman Mappings settings page

**Files:**
- Create: `frontend/src/screens/SpoolmanMappingsPage.tsx`
- Modify: `frontend/src/screens/SettingsScreen.tsx`

- [ ] **Step 1: Create `SpoolmanMappingsPage.tsx`**

Create `frontend/src/screens/SpoolmanMappingsPage.tsx`:

```typescript
import { useEffect, useMemo, useState } from 'react';
import { fetchPrinters, type ApiPrinter } from '../api/printers';
import { getPrinterProfiles } from '../api/queue';
import { fetchFilaments, filamentDisplayName, parseOrcaProfiles, patchFilamentOrcaProfiles, type ApiFilament } from '../api/spoolman';
import { FilamentProfileMultiSelect } from '../components/FilamentProfileMultiSelect';

// Map from machine preset name → one printer ID that uses it (for profile lookup)
function buildPresetPrinterMap(printers: ApiPrinter[]): Map<string, number> {
  const map = new Map<string, number>();
  for (const p of printers) {
    if (p.current_orca_printer_profile && !map.has(p.current_orca_printer_profile)) {
      map.set(p.current_orca_printer_profile, p.id);
    }
  }
  return map;
}

function FilamentCard({ filament, presetPrinterMap, presetProfiles, initiallyExpanded, onRemove }: {
  filament: ApiFilament;
  presetPrinterMap: Map<string, number>;
  presetProfiles: Map<string, string[]>;
  initiallyExpanded: boolean;
  onRemove: () => void;
}) {
  const [expanded, setExpanded] = useState(initiallyExpanded);
  const [draft, setDraft] = useState<Record<string, string[]>>(() => parseOrcaProfiles(filament));
  const [saved, setSaved] = useState<Record<string, string[]>>(() => parseOrcaProfiles(filament));
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const dirty = JSON.stringify(draft) !== JSON.stringify(saved);

  function updatePreset(preset: string, profiles: string[]) {
    setDraft(d => {
      const next = { ...d };
      if (profiles.length === 0) delete next[preset];
      else next[preset] = profiles;
      return next;
    });
    setSaveMsg(null);
  }

  async function save() {
    setSaving(true);
    setSaveMsg(null);
    try {
      await patchFilamentOrcaProfiles(filament.id, draft);
      setSaved({ ...draft });
      setSaveMsg({ ok: true, text: 'Saved' });
      // If all presets cleared, trigger remove from page
      if (Object.keys(draft).length === 0) onRemove();
    } catch (e) {
      setSaveMsg({ ok: false, text: e instanceof Error ? e.message : 'Save failed' });
    } finally {
      setSaving(false);
    }
  }

  const color = filament.color_hex ? `#${filament.color_hex}` : '#888';
  const presets = Array.from(presetPrinterMap.keys());

  // Orphaned: presets in saved mapping not covered by any registered printer
  const orphanedPresets = Object.keys(saved).filter(p => !presetPrinterMap.has(p));

  return (
    <div style={{ background: 'var(--bg-1)', border: '1px solid var(--border-1)', borderRadius: 10, overflow: 'hidden' }}>
      <div
        className="row gap-3"
        style={{ padding: '12px 16px', cursor: 'pointer', alignItems: 'center' }}
        onClick={() => setExpanded(e => !e)}
      >
        <div style={{ width: 12, height: 12, borderRadius: 3, flexShrink: 0, background: color, border: '1px solid var(--border-2)' }} />
        <div className="col" style={{ flex: 1, minWidth: 0 }}>
          <div className="small" style={{ fontWeight: 500 }}>{filamentDisplayName(filament)}</div>
          <div className="tiny muted">{filament.material}</div>
        </div>
        {Object.keys(saved).length > 0 && (
          <span className="tiny" style={{ color: 'var(--ok)' }}>
            {Object.keys(saved).length} preset{Object.keys(saved).length > 1 ? 's' : ''} mapped
          </span>
        )}
        <span className="tiny muted">{expanded ? '▲' : '▼'}</span>
      </div>

      {expanded && (
        <div style={{ borderTop: '1px solid var(--border-1)', padding: '12px 16px' }}>
          <div className="col gap-3">
            {presets.map(preset => {
              const available = presetProfiles.get(preset) ?? [];
              return (
                <div key={preset} className="col gap-1">
                  <label className="tiny muted">{preset}</label>
                  <FilamentProfileMultiSelect
                    profiles={available}
                    selected={draft[preset] ?? []}
                    onChange={profiles => updatePreset(preset, profiles)}
                    emptyText="No compatible filament presets found for this printer"
                  />
                </div>
              );
            })}

            {orphanedPresets.map(preset => (
              <div key={preset} className="col gap-1">
                <label className="tiny" style={{ color: 'var(--warn)' }}>
                  {preset} <span className="muted">(no matching printer registered)</span>
                </label>
                <div className="tiny muted">
                  Saved: {(saved[preset] ?? []).join(', ') || '—'}
                </div>
              </div>
            ))}
          </div>

          <div className="row gap-2" style={{ marginTop: 14, alignItems: 'center' }}>
            <button
              className="btn primary sm"
              disabled={!dirty || saving}
              onClick={save}
            >
              {saving ? 'Saving…' : 'Save'}
            </button>
            {saveMsg && (
              <span className="tiny" style={{ color: saveMsg.ok ? 'var(--ok)' : 'var(--err)' }}>
                {saveMsg.text}
              </span>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export function SpoolmanMappingsPage() {
  const [filaments, setFilaments] = useState<ApiFilament[]>([]);
  const [printers, setPrinters] = useState<ApiPrinter[]>([]);
  const [presetProfiles, setPresetProfiles] = useState<Map<string, string[]>>(new Map());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Filaments actively being edited (added via search)
  const [activeIds, setActiveIds] = useState<Set<number>>(new Set());
  const [searchQuery, setSearchQuery] = useState('');
  const [searchOpen, setSearchOpen] = useState(false);

  const presetPrinterMap = useMemo(() => buildPresetPrinterMap(printers), [printers]);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    Promise.all([fetchFilaments(), fetchPrinters()])
      .then(async ([fils, prns]) => {
        if (!alive) return;
        setFilaments(fils);
        setPrinters(prns);

        // Seed activeIds from filaments that already have mappings
        const withMappings = new Set(
          fils.filter(f => Object.keys(parseOrcaProfiles(f)).length > 0).map(f => f.id)
        );
        setActiveIds(withMappings);

        // Fetch filament profiles per unique machine preset
        const presetMap = buildPresetPrinterMap(prns);
        const profileMap = new Map<string, string[]>();
        await Promise.all(
          Array.from(presetMap.entries()).map(async ([preset, printerId]) => {
            try {
              const p = await getPrinterProfiles(printerId);
              profileMap.set(preset, p.filament_profiles);
            } catch {
              profileMap.set(preset, []);
            }
          })
        );
        if (alive) setPresetProfiles(profileMap);
      })
      .catch(e => { if (alive) setError(e instanceof Error ? e.message : 'Load failed'); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, []);

  const activeFilaments = useMemo(
    () => filaments.filter(f => activeIds.has(f.id)),
    [filaments, activeIds]
  );

  const searchResults = useMemo(() => {
    if (!searchQuery) return [];
    const q = searchQuery.toLowerCase();
    return filaments
      .filter(f => !activeIds.has(f.id))
      .filter(f =>
        filamentDisplayName(f).toLowerCase().includes(q) ||
        f.material.toLowerCase().includes(q) ||
        (f.vendor?.name ?? '').toLowerCase().includes(q)
      )
      .slice(0, 8);
  }, [filaments, activeIds, searchQuery]);

  function addFilament(f: ApiFilament) {
    setActiveIds(ids => new Set([...ids, f.id]));
    setSearchQuery('');
    setSearchOpen(false);
  }

  function removeFilament(id: number) {
    setActiveIds(ids => { const next = new Set(ids); next.delete(id); return next; });
  }

  if (loading) {
    return <div className="small muted" style={{ padding: 24 }}>Loading filaments…</div>;
  }
  if (error) {
    return (
      <div style={{ padding: '12px 16px', background: 'var(--bg-1)', border: '1px solid var(--err)', borderRadius: 8, color: 'var(--err)', fontSize: 13 }}>
        {error}
      </div>
    );
  }
  if (printers.filter(p => p.current_orca_printer_profile).length === 0) {
    return (
      <div className="card" style={{ padding: 28 }}>
        <div className="small muted">
          No printers with an OrcaSlicer machine preset configured. Set a machine preset in Fleet → Edit printer first.
        </div>
      </div>
    );
  }

  return (
    <div className="col gap-3">
      <div className="card" style={{ padding: 28 }}>
        <div style={{ marginBottom: 18 }}>
          <h2 style={{ margin: 0, fontSize: 20, fontWeight: 600, letterSpacing: '-0.01em' }}>Filament Profile Mappings</h2>
          <div className="muted small" style={{ marginTop: 4 }}>
            Map Spoolman filaments to OrcaSlicer filament presets per printer model. Saved to the filament's <code>orca_profiles</code> custom field in Spoolman.
          </div>
        </div>

        {/* Search / add filament */}
        <div style={{ position: 'relative', marginBottom: 16 }}>
          <input
            className="input"
            placeholder="Search filaments to configure…"
            value={searchQuery}
            onChange={e => { setSearchQuery(e.target.value); setSearchOpen(true); }}
            onFocus={() => setSearchOpen(true)}
            onBlur={() => setTimeout(() => setSearchOpen(false), 150)}
          />
          {searchOpen && searchResults.length > 0 && (
            <div style={{
              position: 'absolute', top: '100%', left: 0, right: 0, zIndex: 50,
              background: 'var(--bg-2)', border: '1px solid var(--border-2)',
              borderRadius: 8, boxShadow: '0 8px 24px rgba(0,0,0,0.3)',
              marginTop: 2,
            }}>
              {searchResults.map(f => (
                <div
                  key={f.id}
                  onMouseDown={() => addFilament(f)}
                  style={{ padding: '9px 14px', cursor: 'pointer', fontSize: 13, borderBottom: '1px solid var(--border-1)' }}
                  onMouseEnter={e => (e.currentTarget.style.background = 'var(--bg-3)')}
                  onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                >
                  <span style={{ fontWeight: 500 }}>{filamentDisplayName(f)}</span>
                  <span className="tiny muted" style={{ marginLeft: 8 }}>{f.material}</span>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Filament cards */}
        {activeFilaments.length === 0 ? (
          <div className="tiny muted">
            No filaments configured yet. Search above to add one.
          </div>
        ) : (
          <div className="col gap-2">
            {activeFilaments.map(f => (
              <FilamentCard
                key={f.id}
                filament={f}
                presetPrinterMap={presetPrinterMap}
                presetProfiles={presetProfiles}
                initiallyExpanded={Object.keys(parseOrcaProfiles(f)).length === 0}
                onRemove={() => removeFilament(f.id)}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Register the page in `SettingsScreen.tsx`**

Open `frontend/src/screens/SettingsScreen.tsx`.

**Replace the `PageId` type** (currently: `type PageId = 'tags' | 'print' | 'spoolman' | 'about';`):

```typescript
type PageId = 'tags' | 'print' | 'spoolman' | 'spoolman-mappings' | 'about';
```

**Replace the `PAGE_IDS` array** (currently: `const PAGE_IDS: PageId[] = ['tags', 'print', 'spoolman', 'about'];`):

```typescript
const PAGE_IDS: PageId[] = ['tags', 'print', 'spoolman', 'spoolman-mappings', 'about'];
```

**In the `sections` array inside `SettingsScreen`, replace the Integrations section**:

```typescript
{
  label: 'Integrations',
  items: [
    { id: 'spoolman',          label: 'Spoolman',         icon: SettingsIcons.spoolman, sub: 'Sync filament inventory' },
    { id: 'spoolman-mappings', label: 'Filament Mappings', icon: SettingsIcons.spoolman, sub: 'orca_profiles per printer model' },
  ],
},
```

**Add import at the top of the file** (after the last import):

```typescript
import { SpoolmanMappingsPage } from './SpoolmanMappingsPage';
```

**Add the page renderer** in the page content div (after the `spoolman` renderer):

```typescript
{activePage === 'spoolman-mappings' && <SpoolmanMappingsPage />}
```

- [ ] **Step 3: Verify TypeScript compiles**

```
cd frontend
npm run build 2>&1 | head -40
```

Expected: no errors

- [ ] **Step 4: Commit**

```
git add frontend/src/screens/SpoolmanMappingsPage.tsx frontend/src/screens/SettingsScreen.tsx
git commit -m "feat: Spoolman Filament Mappings settings page"
```

---

### Task 6: PerPrinterConfig — mapping-aware filament profile section

**Files:**
- Modify: `frontend/src/components/PerPrinterConfig.tsx`

Context: `PerPrinterConfig` already has `usePrinterProfiles` (returns `printProfiles` and `filamentProfiles`), `useFilaments` (returns all Spoolman filaments), `spoolmanActive`, and `config.filamentId`. When `filamentId != null`, look up the filament, parse its `orca_profiles`, and show a profile dropdown.

- [ ] **Step 1: Add `parseOrcaProfiles` and `FilamentProfileSelect` imports**

In `frontend/src/components/PerPrinterConfig.tsx`, add to the existing imports:

```typescript
import { parseOrcaProfiles } from '../api/spoolman';
import { FilamentProfileSelect } from './FilamentProfileSelect';
```

- [ ] **Step 2: Add `filamentProfiles` to `usePrinterProfiles` destructure**

Find the existing line:
```typescript
const { printProfiles } = usePrinterProfiles(pid);
```

Replace with:
```typescript
const { printProfiles, filamentProfiles } = usePrinterProfiles(pid);
```

- [ ] **Step 3: Compute `mappedProfiles` from selected Spoolman filament**

Add the following computed values after the `filaments` line (after `const filaments = useFilaments(spoolmanActive);`):

```typescript
const selectedFilament = config.filamentId != null
  ? filaments.find(f => f.id === config.filamentId) ?? null
  : null;

const mappedProfiles: string[] | null = useMemo(() => {
  if (!selectedFilament || !printer?.current_orca_printer_profile) return null;
  const orcaProfiles = parseOrcaProfiles(selectedFilament);
  const list = orcaProfiles[printer.current_orca_printer_profile];
  return list && list.length > 0 ? list : null;
}, [selectedFilament, printer]);
```

Add `useMemo` to the React import at the top of the file (if not already present):
```typescript
import { useEffect, useMemo, useState } from 'react';
```

- [ ] **Step 4: Add filament profile section to the JSX**

In the returned JSX of `PerPrinterConfig`, after the closing `</div>` of the `display: 'grid'` section (the 2-column grid with print profile and filament), add:

```typescript
{/* Filament profile section — appears when Spoolman filament is selected */}
{spoolmanActive && config.filamentId != null && (
  <div style={{ marginTop: 12 }}>
    <label className="label">Filament profile</label>
    {mappedProfiles !== null && mappedProfiles.length === 1 ? (
      <div className="tiny" style={{ marginTop: 4, color: 'var(--text-2)' }}>
        {config.filamentProfile === mappedProfiles[0] ? (
          <>Auto-set: <span className="mono">{mappedProfiles[0]}</span> (from Spoolman mapping)</>
        ) : (
          <FilamentProfileSelect
            profiles={mappedProfiles}
            value={config.filamentProfile ?? null}
            onChange={v => onChange({ filamentProfile: v })}
            placeholder="— use printer default —"
          />
        )}
      </div>
    ) : (
      <FilamentProfileSelect
        profiles={mappedProfiles ?? filamentProfiles}
        value={config.filamentProfile ?? null}
        onChange={v => onChange({ filamentProfile: v })}
        placeholder="— use printer default —"
      />
    )}
    {mappedProfiles !== null && (
      <div className="tiny muted" style={{ marginTop: 4 }}>
        {mappedProfiles.length === 1
          ? 'Profile set from Spoolman mapping. Select another to override.'
          : `Showing ${mappedProfiles.length} profiles from Spoolman mapping.`}
      </div>
    )}
  </div>
)}
```

- [ ] **Step 5: Auto-set `filamentProfile` when exactly one profile is mapped**

Add a `useEffect` after the `mappedProfiles` computation:

```typescript
useEffect(() => {
  if (mappedProfiles !== null && mappedProfiles.length === 1 && config.filamentProfile !== mappedProfiles[0]) {
    onChange({ filamentProfile: mappedProfiles[0] });
  }
  if (mappedProfiles === null && config.filamentId == null) {
    onChange({ filamentProfile: null });
  }
}, [mappedProfiles, config.filamentId]); // eslint-disable-line react-hooks/exhaustive-deps
```

- [ ] **Step 6: Reset `filamentProfile` when filament is cleared**

Find the existing `clearAsk` function:
```typescript
function clearAsk() {
  onChange({ filamentProfile: null, filamentId: null, filamentType: null, filamentColor: null });
}
```

This already clears `filamentProfile` — no change needed.

- [ ] **Step 7: Verify TypeScript compiles**

```
cd frontend
npm run build 2>&1 | head -40
```

Expected: no errors

- [ ] **Step 8: Commit**

```
git add frontend/src/components/PerPrinterConfig.tsx
git commit -m "feat(PerPrinterConfig): mapping-aware filament profile section from Spoolman orca_profiles"
```

---

### Task 7: Fleet FilamentPicker — mapping-aware profile picker

**Files:**
- Modify: `frontend/src/screens/FleetScreen.tsx`

Context: `FilamentPicker` in `FleetScreen.tsx` (around line 258) already fetches `filamentProfiles` via `getPrinterProfiles(printerId)` and renders a plain `<select>` for `filament_profile` per slot (around line 349). It also uses `useSpools`. We need to:
1. Fetch all Spoolman filaments (to read `orca_profiles`)
2. Fetch the printer's `current_orca_printer_profile`
3. When a spool is selected for a slot, auto-apply the mapping

- [ ] **Step 1: Add imports to `FleetScreen.tsx`**

Add to the existing imports at the top of `FleetScreen.tsx`:

```typescript
import { useFilaments, filamentDisplayName, parseOrcaProfiles } from '../api/spoolman';
import { FilamentProfileSelect } from '../components/FilamentProfileSelect';
```

- [ ] **Step 2: Add `useFilaments` and printer profile fetch to `FilamentPicker`**

Inside the `FilamentPicker` component function, after the existing state declarations (after `const [error, setError] = useState<string | null>(null);`), add:

```typescript
const filaments = useFilaments(spoolmanActive);
const [machinePreset, setMachinePreset] = useState<string | null>(null);
```

In the existing `useEffect` inside `FilamentPicker` (the one that calls `getPrinterProfiles` and `fetchPrinter`), add extraction of `current_orca_printer_profile`:

Find this block:
```typescript
  fetchPrinter(printerId)
    .then(p => { if (alive) setSlots(p.loaded_filaments ?? []); })
    .catch(() => {});
```

Replace with:
```typescript
  fetchPrinter(printerId)
    .then(p => {
      if (alive) {
        setSlots(p.loaded_filaments ?? []);
        setMachinePreset(p.current_orca_printer_profile ?? null);
      }
    })
    .catch(() => {});
```

- [ ] **Step 3: Add `getMappedProfiles` helper inside `FilamentPicker`**

Add this helper function inside the `FilamentPicker` component body (before the `return`):

```typescript
function getMappedProfiles(slotSpoolmanSpoolId: string | null | undefined): string[] | null {
  if (!slotSpoolmanSpoolId || !machinePreset) return null;
  const spool = spools.find(s => String(s.id) === slotSpoolmanSpoolId);
  if (!spool) return null;
  const filament = filaments.find(f => f.id === spool.filament.id);
  if (!filament) return null;
  const orcaProfiles = parseOrcaProfiles(filament);
  const list = orcaProfiles[machinePreset];
  return list && list.length > 0 ? list : null;
}
```

- [ ] **Step 4: Replace the filament profile `<select>` with `FilamentProfileSelect` + auto-apply**

Find the existing filament profile select inside the slot map (around line 347):

```typescript
<div className="col gap-1" style={{ flex: '1 1 180px' }}>
  <label className="tiny muted" htmlFor={`fp-${i}`}>Filament profile</label>
  <select
    id={`fp-${i}`}
    className="input"
    value={s.filament_profile ?? ''}
    onChange={e => updateSlot(i, { filament_profile: e.target.value || null })}>
    <option value="">— none (slicer default) —</option>
    {filamentProfiles.map(fp => <option key={fp} value={fp}>{fp}</option>)}
  </select>
</div>
```

Replace with:

```typescript
<div className="col gap-1" style={{ flex: '1 1 180px' }}>
  <label className="tiny muted" htmlFor={`fp-${i}`}>Filament profile</label>
  {(() => {
    const mapped = getMappedProfiles(s.spoolman_spool_id);
    const profiles = mapped ?? filamentProfiles;
    return (
      <FilamentProfileSelect
        profiles={profiles}
        value={s.filament_profile ?? null}
        onChange={v => updateSlot(i, { filament_profile: v })}
        placeholder="— none (slicer default) —"
      />
    );
  })()}
  {getMappedProfiles(s.spoolman_spool_id) !== null && (
    <div className="tiny muted" style={{ marginTop: 2 }}>From Spoolman mapping</div>
  )}
</div>
```

- [ ] **Step 5: Auto-apply single mapped profile when spool is picked**

Find the existing `pickSpool` function:

```typescript
function pickSpool(i: number, spoolId: string) {
  if (!spoolId) { updateSlot(i, { spoolman_spool_id: null }); return; }
  const sp = spools.find(s => String(s.id) === spoolId);
  if (!sp) return;
  updateSlot(i, {
    spoolman_spool_id: String(sp.id),
    name: spoolDisplayName(sp),
    type: sp.filament.material,
    color: sp.filament.color_hex ? `#${sp.filament.color_hex}` : (slots[i]?.color ?? '#888888'),
  });
}
```

Replace with:

```typescript
function pickSpool(i: number, spoolId: string) {
  if (!spoolId) { updateSlot(i, { spoolman_spool_id: null, filament_profile: null }); return; }
  const sp = spools.find(s => String(s.id) === spoolId);
  if (!sp) return;

  const filament = filaments.find(f => f.id === sp.filament.id);
  const mapped = filament && machinePreset
    ? (() => {
        const list = parseOrcaProfiles(filament)[machinePreset];
        return list && list.length > 0 ? list : null;
      })()
    : null;

  updateSlot(i, {
    spoolman_spool_id: String(sp.id),
    name: spoolDisplayName(sp),
    type: sp.filament.material,
    color: sp.filament.color_hex ? `#${sp.filament.color_hex}` : (slots[i]?.color ?? '#888888'),
    filament_profile: mapped && mapped.length === 1 ? mapped[0] : (slots[i]?.filament_profile ?? null),
  });
}
```

- [ ] **Step 6: Verify TypeScript compiles**

```
cd frontend
npm run build 2>&1 | head -40
```

Expected: no errors

- [ ] **Step 7: Commit**

```
git add frontend/src/screens/FleetScreen.tsx
git commit -m "feat(fleet): mapping-aware filament profile picker in FilamentPicker"
```

---

## Self-Review Checklist

After all tasks complete, verify:

- [ ] `PATCH /api/v1/spoolman/filaments/{id}` endpoint exists and is tested
- [ ] `ApiFilament.extra` is typed and `parseOrcaProfiles` handles malformed JSON gracefully
- [ ] Mappings page appears under Settings → Integrations → Filament Mappings (only when Spoolman configured)
- [ ] `FilamentProfileMultiSelect` chips are removable, dropdown closes on blur
- [ ] `PerPrinterConfig`: auto-sets `filamentProfile` when exactly 1 mapped; restricts dropdown when multiple; shows all when none
- [ ] Fleet `FilamentPicker`: auto-applies profile on `pickSpool` when exactly 1 mapped
- [ ] No regression in existing PerPrinterConfig behavior when Spoolman is disabled or no filament selected
