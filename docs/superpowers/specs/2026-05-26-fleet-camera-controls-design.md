# Fleet Screen — Camera Feed & Printer Controls Design

**Date:** 2026-05-26  
**Branch:** feat/fleet-camera-controls  
**Scope:** Wire the camera feed in `PrinterExpandedCard`, and add live printer controls (pause/resume/stop, chamber light, Z-jog, fans, bed temperature).

---

## Problem

Two gaps remain in the Fleet screen's expanded card after the live-wiring feature:

1. **Camera feed** — `VideoTile` renders a static placeholder stub. The backend camera endpoint (`GET /api/v1/printers/{id}/camera`) already exists and streams MJPEG, but the frontend never calls it.
2. **Printer controls** — Pause/Stop buttons are stubs with no API calls. Chamber light, Z-jog, fan control, and bed temperature controls are entirely missing despite the ABC defining the underlying methods.

---

## Architecture

### Camera

`VideoTile` (in `frontend/src/components/ui.tsx`) gains a `printerId?: string` prop. When `printerId` is set and `live` is true, it renders:

```html
<img
  src="/api/v1/printers/{printerId}/camera"
  style="width:100%;height:100%;object-fit:cover"
  onError={showPlaceholder}
/>
```

On error (printer offline, no camera capability, stream drop) it falls back to the existing placeholder. The printer ID is already available in `PrinterExpandedCard` as `p.id`.

**Elegoo stream activation:** The Elegoo Centauri requires SDCP Cmd 386 (`start_video_stream`) to activate the MJPEG stream before the URL is live, plus periodic pings (~every 45 s) to reset the printer's 60-second inactivity timer. The existing camera route (`GET /api/v1/printers/{id}/camera` in `printers.py`) is enhanced to:
1. Call `client.start_video_stream()` to get the active URL (already implemented on `ElegooCentauriClient`)
2. Send `ping_video_stream()` pings in a background task while the streaming response is open

No new endpoint — this is an internal fix to the existing route.

### Backend controls layer

**New `PrinterCapabilities` flags** (added to `abstract_printer_client.py`):
- `fan_control: bool = False`
- `temp_control: bool = False`

The Elegoo's `PrinterCapabilities` dataclass (returned inline, not a method) sets both to `True`.

**New ABC methods** (concrete default returns `False`; Elegoo overrides both):

```python
def set_fan_speeds(self, model_pct: int, aux_pct: int, box_pct: int) -> bool:
    return False

def set_bed_temp(self, celsius: int) -> bool:
    return False
```

**Elegoo implementations** (both via Cmd 403):

```python
def set_fan_speeds(self, model_pct, aux_pct, box_pct) -> bool:
    return self._send(_CMD_EDIT_STATUS_DATA,
        {"TargetFanSpeed": {"ModelFan": model_pct, "AuxiliaryFan": aux_pct, "BoxFan": box_pct}},
        wait_ack=True)

def set_bed_temp(self, celsius) -> bool:
    return self._send(_CMD_EDIT_STATUS_DATA,
        {"TempTargetHotbed": celsius},
        wait_ack=True)
```

**`ElegooState` additions:**
- `fan_box: int = 0` — maps `CurrentFanSpeed.BoxFan` (was missing from local reference doc)
- Status parsing updated to read `BoxFan`
- `_elegoo_state_to_dict` updated to expose `fan_model`, `fan_aux`, `fan_box` in the normalized state dict (replacing the current single `fan_speed` field)

**Seven new REST endpoints** in `backend/app/api/routes/printers.py` — all follow the same guard pattern (404 if printer not found, 503 if not connected, return `{"ok": true}`):

| Route | Body | ABC call |
|---|---|---|
| `POST /{id}/pause` | — | `client.pause_print()` |
| `POST /{id}/resume` | — | `client.resume_print()` |
| `POST /{id}/stop` | — | `client.stop_print()` |
| `POST /{id}/light` | `{on: bool}` | `client.set_chamber_light(on)` |
| `POST /{id}/jog-z` | `{distance_mm: float}` | `client.jog_z(distance_mm)` |
| `POST /{id}/fan` | `{fan: "model"\|"auxiliary"\|"box", speed_pct: int 0–100}` | Reads current fan state, calls `client.set_fan_speeds(...)` with updated value |
| `POST /{id}/bed-temp` | `{celsius: int}` — 0 means off | `client.set_bed_temp(celsius)` |

The fan endpoint reads current `fan_model`/`fan_aux`/`fan_box` from `printer_manager.get_normalized_state(id)` before calling `set_fan_speeds`, so changing one fan doesn't reset the others.

### Frontend API module

Seven new functions added to `frontend/src/api/printers.ts`:

```typescript
pausePrinter(id: string): Promise<void>
resumePrinter(id: string): Promise<void>
stopPrinter(id: string): Promise<void>
setLight(id: string, on: boolean): Promise<void>
jogZ(id: string, distanceMm: number): Promise<void>
setFanSpeed(id: string, fan: 'model' | 'auxiliary' | 'box', speedPct: number): Promise<void>
setBedTemp(id: string, celsius: number): Promise<void>
```

All throw on non-OK response.

### Data model changes

**`Printer` type** (`frontend/src/data/types.ts`) gains:
```typescript
fanModel: number      // 0–100, part cooling
fanAux: number        // 0–100, auxiliary
fanBox: number        // 0–100, box/chamber
bedTempTarget: number // TempTargetHotbed — shown in bed temp card as current target
```

**`FleetPrinter` interface** (`frontend/src/api/fleet.ts`) gains:
```typescript
fan_model: number
fan_aux: number
fan_box: number
```

**`toFleetPrinter` mapper** maps these directly: `fanModel: p.fan_model`, `fanAux: p.fan_aux`, `fanBox: p.fan_box`, `bedTempTarget: p.temperatures?.bed_target ?? 0`.

**`GET /api/v1/fleet`** already merges the full normalized state dict — once `fan_model`/`fan_aux`/`fan_box` are added to `_elegoo_state_to_dict`, they flow through automatically.

### Frontend controls UI

Changes are entirely within `PrinterExpandedCard` in `FleetScreen.tsx`.

**VideoTile call** — `printerId` passed in:
```tsx
<VideoTile live={isPrinting} time={p.timeElapsed} printerId={p.id} />
```

**Action row** (left column, already exists) — extended:
- `{isPrinting && <button onClick={() => pausePrinter(p.id)}>Pause</button>}`
- `{p.status === 'paused' && <button onClick={() => resumePrinter(p.id)}>Resume</button>}`
- `{(isPrinting || p.status === 'paused') && <button onClick={() => stopPrinter(p.id)}>Stop</button>}`
- Chamber light: inline toggle button (not a separate component) with local `useState(false)` tracking on/off optimistically — shown when `p.capabilities.includes('chamber light')`
- Z-jog buttons (`+10mm` / `−10mm`) shown when `p.capabilities.includes('pause resume')`. The ABC's `jog_z` default uses `send_gcode`, but `ElegooCentauriClient` overrides it with Cmd 401 — gating on `pause_resume` rather than `gcode` ensures the button appears on Elegoo (where `gcode=False`).

**New "Fans" card** (right column, shown when `p.capabilities.includes('fan control')`):

Three labeled rows — Model, Auxiliary, Box — each with a slider (0–100) and percentage readout. Sliders initialize to `p.fanModel`, `p.fanAux`, `p.fanBox`. `onChange` is debounced 300 ms before calling `setFanSpeed`.

**New "Bed temp" card** (right column, shown when `p.capabilities.includes('temp control')`):

Preset chips: `Off · 60 · 80 · 95 · 110°C`, plus a number input and Set button. Current target temp shown from `p.bedTemp` (already mapped from `TempTargetHotbed` in the normalized state).

---

## Reference doc patch

`docs/elegoo-centauri-client.md` is updated to document:
- `BoxFan` in the fan fields section
- `TargetFanSpeed` write payload for Cmd 403
- `TempTargetHotbed` / `TempTargetNozzle` write payloads for Cmd 403
- `PrintSpeedPct` write payload for Cmd 403 (discovered in community implementation, useful for future)
- `fan_box` field in `ElegooState`

---

## Error handling

- All control endpoints return `503` if the printer is not connected. The frontend shows a toast/console error on non-OK responses (no retry logic — user retries manually).
- Camera `onError`: falls back to placeholder immediately, no retry loop (the `<img>` browser retry on navigation handles reconnection).
- Fan sliders are disabled (grayed) when printer `status === 'offline'`.
- Bed temp input is disabled when `status === 'offline'`.

---

## Testing

### Backend
- `tests/test_printer_controls.py` — one test per endpoint: 404 on missing printer, 503 on disconnected, 200+`{ok:true}` on success (mocked client). Fan endpoint test: verifies all three fan values sent, not just the changed one.
- `tests/unit/services/test_elegoo_centauri_client.py` — unit tests for `set_fan_speeds` (asserts Cmd 403 with `TargetFanSpeed` payload) and `set_bed_temp` (asserts Cmd 403 with `TempTargetHotbed` payload), following the existing mock seam pattern.

### Frontend
- `api/printers.test.ts` — unit tests for each new fetch function (mocked fetch, verifies URL and method).
- `FleetScreen.test.tsx` — extend existing tests: wired Pause calls `pausePrinter`, light toggle fires `setLight`, fan slider debounce fires `setFanSpeed`. Mock `../api/printers` module.

---

## Out of scope

- Nozzle temperature control (unsafe to expose without filament context; can be added later)
- Print speed adjustment (Cmd 403 `PrintSpeedPct` — discovered but deferred)
- Chamber temperature target (no write command found in SDCP)
- Bambu printer controls (Bambu MQTT client is a separate implementation; fan/temp commands differ)
- Camera feed on the compact `PrinterTile` (grid/list view) — only the expanded card
