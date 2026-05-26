# Fleet Camera + Printer Controls Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the camera feed in `PrinterExpandedCard` and add live printer controls (pause/resume/stop, chamber light, Z-jog, fans, bed temperature).

**Architecture:** Backend gains 7 new REST control endpoints and an enhanced camera proxy route (Elegoo stream activation + ping keepalive). Frontend gains API functions, new fields on data types, a wired `VideoTile`, and a controls UI in `PrinterExpandedCard`.

**Tech Stack:** FastAPI + pytest-asyncio (backend); React 18 + TypeScript + Vitest + Testing Library (frontend).

---

## File Map

| File | Change |
|---|---|
| `docs/elegoo-centauri-client.md` | Add BoxFan, TargetFanSpeed, temp write payloads for Cmd 403 |
| `backend/app/services/elegoo_centauri_client.py` | Add `fan_box` to `ElegooState`, parse `BoxFan`, add `set_fan_speeds` + `set_bed_temp` |
| `backend/app/services/abstract_printer_client.py` | Add `fan_control` + `temp_control` capability flags; add default `set_fan_speeds` + `set_bed_temp` |
| `backend/app/services/printer_manager.py` | Replace `fan_speed` with `fan_model`/`fan_aux`/`fan_box` in `_serialize_elegoo` |
| `backend/app/api/routes/printers.py` | Add 7 control endpoints; enhance camera route |
| `backend/tests/services/test_elegoo_centauri.py` | Extend with fan_box parsing, set_fan_speeds, set_bed_temp tests |
| `backend/tests/services/test_abstract_client.py` | Add default set_fan_speeds + set_bed_temp tests |
| `backend/tests/api/test_printer_controls.py` | New — integration tests for 7 control endpoints |
| `backend/tests/api/test_camera_stream.py` | New — camera route enhancement tests |
| `frontend/src/data/types.ts` | Add `fanModel`, `fanAux`, `fanBox`, `bedTempTarget` to `Printer` |
| `frontend/src/api/fleet.ts` | Add `fan_model`/`fan_aux`/`fan_box` to `FleetPrinter`; update `temperatures` type; update mapper |
| `frontend/src/api/printers.ts` | Add 7 new control functions |
| `frontend/src/api/printers.test.ts` | New — unit tests for 7 new functions |
| `frontend/src/api/fleet.test.ts` | Extend with fan + bedTempTarget mapper tests |
| `frontend/src/components/ui.tsx` | Add `printerId` prop to `VideoTile` |
| `frontend/src/components/ui.test.tsx` | Extend with VideoTile camera tests |
| `frontend/src/screens/FleetScreen.tsx` | Pass `printerId` to `VideoTile`; wire controls in `PrinterExpandedCard` |
| `frontend/src/screens/FleetScreen.test.tsx` | Extend with wired control button + fan slider tests |

---

## Task 1: Update reference doc

**Files:**
- Modify: `docs/elegoo-centauri-client.md`

No code tests for documentation. Just update the doc and commit.

- [ ] **Step 1: Update the Command ID table row for Cmd 403**

In the table under "Command ID Reference", replace the row:
```
| 403 | `_CMD_EDIT_STATUS_DATA` | → printer | `{"LightStatus": {...}}` | Light + fan control |
```
with:
```
| 403 | `_CMD_EDIT_STATUS_DATA` | → printer | See write payloads below | Light, fan, temperature control |
```

- [ ] **Step 2: Add write payloads section after the command table**

Add this section after the Command ID Reference table:

```markdown
### Cmd 403 write payloads (`EDIT_STATUS_DATA`)

All payloads are sent as `{"LightStatus": {...}}` or top-level keys in the `Data` dict:

| Purpose | Payload |
|---|---|
| Chamber light (on) | `{"LightStatus": {"SecondLight": true, "RgbLight": [R, G, B]}}` |
| Chamber light (off) | `{"LightStatus": {"SecondLight": false, "RgbLight": [R, G, B]}}` |
| Fan speeds | `{"TargetFanSpeed": {"ModelFan": 80, "AuxiliaryFan": 60, "BoxFan": 40}}` |
| Bed temperature | `{"TempTargetHotbed": 95}` — use `0` to turn off |
| Nozzle temperature | `{"TempTargetNozzle": 220}` — deferred, do not expose yet |
| Print speed | `{"PrintSpeedPct": 100}` — deferred |

**Fan semantics:** All three fan values must be sent together. To change one fan without resetting others, read the current `fan_model`/`fan_aux`/`fan_box` from the state dict, patch the target fan, then send all three.
```

- [ ] **Step 3: Add BoxFan to the fan fields section**

Find the "Fan fields" section:
```
### Fan fields

From `Status.CurrentFanSpeed`:
- `ModelFan` → `state.fan_model` (part-cooling fan, 0–100)
- `AuxiliaryFan` → `state.fan_aux` (0–100)
```

Replace with:
```
### Fan fields

From `Status.CurrentFanSpeed`:
- `ModelFan` → `state.fan_model` (part-cooling fan, 0–100)
- `AuxiliaryFan` → `state.fan_aux` (0–100)
- `BoxFan` → `state.fan_box` (box/chamber fan, 0–100)
```

- [ ] **Step 4: Update the ElegooState dataclass listing in the doc**

Find `fan_aux: int                  # 0–100` and add below it:
```
    fan_box: int                   # 0–100, box/chamber fan
```

- [ ] **Step 5: Commit**

```bash
git add docs/elegoo-centauri-client.md
git commit -m "docs: add BoxFan, fan write payload, and bed temp payload to Elegoo reference"
```

---

## Task 2: ElegooState fan_box + status parsing + serializer update

**Files:**
- Modify: `backend/app/services/elegoo_centauri_client.py`
- Modify: `backend/app/services/printer_manager.py`
- Test: `backend/tests/services/test_elegoo_centauri.py`

- [ ] **Step 1: Write failing tests**

Add to `backend/tests/services/test_elegoo_centauri.py`:

```python
# ---------------------------------------------------------------------------
# fan_box field
# ---------------------------------------------------------------------------

def test_elegoo_state_has_fan_box_defaulting_to_zero():
    client = _make_client()
    assert client.state.fan_box == 0


def test_parse_status_msg_reads_box_fan():
    client = _make_client()
    msg = {
        "Status": {
            "CurrentFanSpeed": {"ModelFan": 80, "AuxiliaryFan": 60, "BoxFan": 40},
            "CurrentStatus": [],
            "PrintInfo": {},
        }
    }
    client._parse_status_msg(msg)
    assert client.state.fan_box == 40


def test_parse_status_msg_box_fan_defaults_to_zero_when_absent():
    client = _make_client()
    msg = {
        "Status": {
            "CurrentFanSpeed": {"ModelFan": 80, "AuxiliaryFan": 60},
            "CurrentStatus": [],
            "PrintInfo": {},
        }
    }
    client._parse_status_msg(msg)
    assert client.state.fan_box == 0


# ---------------------------------------------------------------------------
# Serializer — fan fields
# ---------------------------------------------------------------------------

def test_serialize_elegoo_exposes_three_fan_fields():
    from app.services.printer_manager import _serialize_elegoo
    from app.services.elegoo_centauri_client import ElegooState
    state = ElegooState()
    state.connected = True
    state.fan_model = 80
    state.fan_aux = 60
    state.fan_box = 40
    result = _serialize_elegoo(state, 1)
    assert result["fan_model"] == 80
    assert result["fan_aux"] == 60
    assert result["fan_box"] == 40


def test_serialize_elegoo_no_longer_has_fan_speed():
    from app.services.printer_manager import _serialize_elegoo
    from app.services.elegoo_centauri_client import ElegooState
    result = _serialize_elegoo(ElegooState(), 1)
    assert "fan_speed" not in result
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd backend && pytest tests/services/test_elegoo_centauri.py -k "fan_box or fan_speed or three_fan" -v
```
Expected: FAIL — `ElegooState has no attribute 'fan_box'` and `fan_speed` is present.

- [ ] **Step 3: Add fan_box to ElegooState**

In `backend/app/services/elegoo_centauri_client.py`, find:
```python
    fan_model: int = 0
    fan_aux: int = 0
    chamber_light: bool = False
```
Replace with:
```python
    fan_model: int = 0
    fan_aux: int = 0
    fan_box: int = 0
    chamber_light: bool = False
```

- [ ] **Step 4: Parse BoxFan in _parse_status_msg**

In `_parse_status_msg`, find:
```python
        # Fans
        fans = status.get("CurrentFanSpeed", {})
        new.fan_model = int(fans.get("ModelFan", 0))
        new.fan_aux = int(fans.get("AuxiliaryFan", 0))
```
Replace with:
```python
        # Fans
        fans = status.get("CurrentFanSpeed", {})
        new.fan_model = int(fans.get("ModelFan", 0))
        new.fan_aux = int(fans.get("AuxiliaryFan", 0))
        new.fan_box = int(fans.get("BoxFan", 0))
```

- [ ] **Step 5: Update _serialize_elegoo in printer_manager.py**

In `backend/app/services/printer_manager.py`, find:
```python
        "fan_speed": getattr(state, "fan_model", 0),
```
Replace with:
```python
        "fan_model": getattr(state, "fan_model", 0),
        "fan_aux": getattr(state, "fan_aux", 0),
        "fan_box": getattr(state, "fan_box", 0),
```

- [ ] **Step 6: Run tests to confirm they pass**

```bash
cd backend && pytest tests/services/test_elegoo_centauri.py -k "fan_box or fan_speed or three_fan" -v
```
Expected: PASS (5 tests).

- [ ] **Step 7: Run full test suite to check for regressions**

```bash
cd backend && pytest -v
```
Expected: All pass. If any test fails expecting `fan_speed`, update that test to use `fan_model`.

- [ ] **Step 8: Commit**

```bash
git add backend/app/services/elegoo_centauri_client.py backend/app/services/printer_manager.py backend/tests/services/test_elegoo_centauri.py
git commit -m "feat(backend): add fan_box to ElegooState, parse BoxFan, expose three fan fields in serializer"
```

---

## Task 3: ABC capability flags + set_fan_speeds/set_bed_temp + Elegoo overrides

**Files:**
- Modify: `backend/app/services/abstract_printer_client.py`
- Modify: `backend/app/services/elegoo_centauri_client.py`
- Test: `backend/tests/services/test_elegoo_centauri.py`
- Test: `backend/tests/services/test_abstract_client.py`

- [ ] **Step 1: Write failing tests for ABC defaults**

In `backend/tests/services/test_abstract_client.py`, add:

```python
from app.services.abstract_printer_client import AbstractPrinterClient, PrinterCapabilities


class _Dummy(AbstractPrinterClient):
    printer_type = "dummy"
    @property
    def connected(self): return False
    def connect(self, loop=None): pass
    def disconnect(self, timeout=0): pass
    def check_staleness(self): return False
    def start_print(self, f, opts=None): return False
    def stop_print(self): return False
    def pause_print(self): return False
    def resume_print(self): return False
    def send_gcode(self, g): return False
    def request_status_update(self): pass


def test_capabilities_has_fan_control_flag():
    caps = PrinterCapabilities()
    assert caps.fan_control is False


def test_capabilities_has_temp_control_flag():
    caps = PrinterCapabilities()
    assert caps.temp_control is False


def test_set_fan_speeds_default_returns_false():
    assert _Dummy().set_fan_speeds(100, 100, 100) is False


def test_set_bed_temp_default_returns_false():
    assert _Dummy().set_bed_temp(60) is False
```

- [ ] **Step 2: Write failing tests for Elegoo overrides**

Add to `backend/tests/services/test_elegoo_centauri.py`:

```python
import json

# ---------------------------------------------------------------------------
# New capability flags
# ---------------------------------------------------------------------------

def test_fan_control_capability():
    assert _make_client().get_capabilities().fan_control is True


def test_temp_control_capability():
    assert _make_client().get_capabilities().temp_control is True


# ---------------------------------------------------------------------------
# set_fan_speeds
# ---------------------------------------------------------------------------

def test_set_fan_speeds_sends_cmd_403_with_target_fan_speed():
    client = _connected_client()
    result = client.set_fan_speeds(80, 60, 40)
    assert result is True
    sent = json.loads(client._ws.send.call_args[0][0])
    assert sent["Data"]["Cmd"] == 403
    assert sent["Data"]["Data"]["TargetFanSpeed"] == {
        "ModelFan": 80, "AuxiliaryFan": 60, "BoxFan": 40
    }


def test_set_fan_speeds_all_zero_for_off():
    client = _connected_client()
    client.set_fan_speeds(0, 0, 0)
    sent = json.loads(client._ws.send.call_args[0][0])
    assert sent["Data"]["Data"]["TargetFanSpeed"] == {
        "ModelFan": 0, "AuxiliaryFan": 0, "BoxFan": 0
    }


def test_set_fan_speeds_returns_false_when_not_connected():
    client = _make_client()  # no _ws set
    assert client.set_fan_speeds(80, 60, 40) is False


# ---------------------------------------------------------------------------
# set_bed_temp
# ---------------------------------------------------------------------------

def test_set_bed_temp_sends_cmd_403_with_temp_target_hotbed():
    client = _connected_client()
    result = client.set_bed_temp(95)
    assert result is True
    sent = json.loads(client._ws.send.call_args[0][0])
    assert sent["Data"]["Cmd"] == 403
    assert sent["Data"]["Data"]["TempTargetHotbed"] == 95


def test_set_bed_temp_zero_turns_off():
    client = _connected_client()
    client.set_bed_temp(0)
    sent = json.loads(client._ws.send.call_args[0][0])
    assert sent["Data"]["Data"]["TempTargetHotbed"] == 0


def test_set_bed_temp_returns_false_when_not_connected():
    client = _make_client()
    assert client.set_bed_temp(60) is False
```

- [ ] **Step 3: Run tests to confirm they fail**

```bash
cd backend && pytest tests/services/test_abstract_client.py tests/services/test_elegoo_centauri.py -k "fan_control or temp_control or set_fan or set_bed" -v
```
Expected: FAIL.

- [ ] **Step 4: Add fan_control and temp_control to PrinterCapabilities**

In `backend/app/services/abstract_printer_client.py`, find:
```python
    camera: bool = False
```
Replace with:
```python
    camera: bool = False
    fan_control: bool = False
    temp_control: bool = False
```

- [ ] **Step 5: Add default set_fan_speeds and set_bed_temp to ABC**

In `abstract_printer_client.py`, find:
```python
    def set_chamber_light(self, on: bool) -> bool:
        return False
```
Add after it:
```python
    def set_fan_speeds(self, model_pct: int, aux_pct: int, box_pct: int) -> bool:
        return False

    def set_bed_temp(self, celsius: int) -> bool:
        return False
```

- [ ] **Step 6: Add Elegoo overrides for set_fan_speeds and set_bed_temp**

In `backend/app/services/elegoo_centauri_client.py`, find:
```python
    def set_chamber_light(self, on: bool) -> bool:
```
Add these two methods after `set_chamber_light` (after the closing line of that method):
```python
    def set_fan_speeds(self, model_pct: int, aux_pct: int, box_pct: int) -> bool:
        return self._send(
            _Cmd.EDIT_STATUS_DATA,
            {"TargetFanSpeed": {"ModelFan": model_pct, "AuxiliaryFan": aux_pct, "BoxFan": box_pct}},
        )

    def set_bed_temp(self, celsius: int) -> bool:
        return self._send(_Cmd.EDIT_STATUS_DATA, {"TempTargetHotbed": celsius})
```

- [ ] **Step 7: Set fan_control and temp_control in Elegoo get_capabilities**

In `elegoo_centauri_client.py`, find:
```python
    def get_capabilities(self) -> PrinterCapabilities:
        return PrinterCapabilities(
            camera=True,
            pause_resume=True,
            chamber_light=True,
            bed_levelling=True,
            file_upload=True,
            file_models=True,
            file_history=True,
            gcode=False,
        )
```
Replace with:
```python
    def get_capabilities(self) -> PrinterCapabilities:
        return PrinterCapabilities(
            camera=True,
            pause_resume=True,
            chamber_light=True,
            bed_levelling=True,
            file_upload=True,
            file_models=True,
            file_history=True,
            gcode=False,
            fan_control=True,
            temp_control=True,
        )
```

- [ ] **Step 8: Run tests to confirm they pass**

```bash
cd backend && pytest tests/services/test_abstract_client.py tests/services/test_elegoo_centauri.py -k "fan_control or temp_control or set_fan or set_bed" -v
```
Expected: All pass.

- [ ] **Step 9: Run full suite**

```bash
cd backend && pytest -v
```
Expected: All pass.

- [ ] **Step 10: Commit**

```bash
git add backend/app/services/abstract_printer_client.py backend/app/services/elegoo_centauri_client.py backend/tests/services/test_elegoo_centauri.py backend/tests/services/test_abstract_client.py
git commit -m "feat(backend): add fan_control/temp_control capabilities and set_fan_speeds/set_bed_temp to ABC and Elegoo client"
```

---

## Task 4: 7 new REST control endpoints

**Files:**
- Modify: `backend/app/api/routes/printers.py`
- Create: `backend/tests/api/test_printer_controls.py`

- [ ] **Step 1: Write failing integration tests**

Create `backend/tests/api/test_printer_controls.py`:

```python
import pytest
from unittest.mock import MagicMock, patch
from app.services.printer_manager import printer_manager


async def _create_printer(client) -> int:
    resp = await client.post("/api/v1/printers", json={
        "name": "Test Printer",
        "printer_type": "elegoo_centauri",
        "connection_config": {"ip_address": "192.168.1.99"},
    })
    assert resp.status_code == 201
    return resp.json()["id"]


def _mock_connected_client():
    mock = MagicMock()
    mock.connected = True
    mock.pause_print.return_value = True
    mock.resume_print.return_value = True
    mock.stop_print.return_value = True
    mock.set_chamber_light.return_value = True
    mock.jog_z.return_value = True
    mock.set_fan_speeds.return_value = True
    mock.set_bed_temp.return_value = True
    return mock


# ── Pause ────────────────────────────────────────────────────────────────────

async def test_pause_404_on_missing_printer(client):
    resp = await client.post("/api/v1/printers/999/pause")
    assert resp.status_code == 404


async def test_pause_503_when_not_connected(client):
    printer_id = await _create_printer(client)
    resp = await client.post(f"/api/v1/printers/{printer_id}/pause")
    assert resp.status_code == 503
    printer_manager._clients.pop(printer_id, None)


async def test_pause_ok(client):
    printer_id = await _create_printer(client)
    mock = _mock_connected_client()
    printer_manager._clients[printer_id] = mock
    resp = await client.post(f"/api/v1/printers/{printer_id}/pause")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    mock.pause_print.assert_called_once()
    printer_manager._clients.pop(printer_id)


# ── Resume ───────────────────────────────────────────────────────────────────

async def test_resume_404_on_missing_printer(client):
    resp = await client.post("/api/v1/printers/999/resume")
    assert resp.status_code == 404


async def test_resume_503_when_not_connected(client):
    printer_id = await _create_printer(client)
    resp = await client.post(f"/api/v1/printers/{printer_id}/resume")
    assert resp.status_code == 503
    printer_manager._clients.pop(printer_id, None)


async def test_resume_ok(client):
    printer_id = await _create_printer(client)
    mock = _mock_connected_client()
    printer_manager._clients[printer_id] = mock
    resp = await client.post(f"/api/v1/printers/{printer_id}/resume")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    mock.resume_print.assert_called_once()
    printer_manager._clients.pop(printer_id)


# ── Stop ─────────────────────────────────────────────────────────────────────

async def test_stop_404_on_missing_printer(client):
    resp = await client.post("/api/v1/printers/999/stop")
    assert resp.status_code == 404


async def test_stop_503_when_not_connected(client):
    printer_id = await _create_printer(client)
    resp = await client.post(f"/api/v1/printers/{printer_id}/stop")
    assert resp.status_code == 503
    printer_manager._clients.pop(printer_id, None)


async def test_stop_ok(client):
    printer_id = await _create_printer(client)
    mock = _mock_connected_client()
    printer_manager._clients[printer_id] = mock
    resp = await client.post(f"/api/v1/printers/{printer_id}/stop")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    mock.stop_print.assert_called_once()
    printer_manager._clients.pop(printer_id)


# ── Light ────────────────────────────────────────────────────────────────────

async def test_light_ok_on(client):
    printer_id = await _create_printer(client)
    mock = _mock_connected_client()
    printer_manager._clients[printer_id] = mock
    resp = await client.post(f"/api/v1/printers/{printer_id}/light", json={"on": True})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    mock.set_chamber_light.assert_called_once_with(True)
    printer_manager._clients.pop(printer_id)


async def test_light_ok_off(client):
    printer_id = await _create_printer(client)
    mock = _mock_connected_client()
    printer_manager._clients[printer_id] = mock
    resp = await client.post(f"/api/v1/printers/{printer_id}/light", json={"on": False})
    assert resp.status_code == 200
    mock.set_chamber_light.assert_called_once_with(False)
    printer_manager._clients.pop(printer_id)


# ── Jog-Z ────────────────────────────────────────────────────────────────────

async def test_jog_z_ok(client):
    printer_id = await _create_printer(client)
    mock = _mock_connected_client()
    printer_manager._clients[printer_id] = mock
    resp = await client.post(f"/api/v1/printers/{printer_id}/jog-z", json={"distance_mm": 10.0})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    mock.jog_z.assert_called_once_with(10.0)
    printer_manager._clients.pop(printer_id)


async def test_jog_z_negative(client):
    printer_id = await _create_printer(client)
    mock = _mock_connected_client()
    printer_manager._clients[printer_id] = mock
    resp = await client.post(f"/api/v1/printers/{printer_id}/jog-z", json={"distance_mm": -10.0})
    assert resp.status_code == 200
    mock.jog_z.assert_called_once_with(-10.0)
    printer_manager._clients.pop(printer_id)


# ── Fan ──────────────────────────────────────────────────────────────────────

async def test_fan_ok_changes_model_fan_preserves_others(client):
    printer_id = await _create_printer(client)
    mock = _mock_connected_client()
    printer_manager._clients[printer_id] = mock
    with patch.object(printer_manager, "get_normalized_state", return_value={
        "fan_model": 50, "fan_aux": 60, "fan_box": 40,
    }):
        resp = await client.post(
            f"/api/v1/printers/{printer_id}/fan",
            json={"fan": "model", "speed_pct": 100},
        )
    assert resp.status_code == 200
    mock.set_fan_speeds.assert_called_once_with(100, 60, 40)
    printer_manager._clients.pop(printer_id)


async def test_fan_ok_changes_aux_fan(client):
    printer_id = await _create_printer(client)
    mock = _mock_connected_client()
    printer_manager._clients[printer_id] = mock
    with patch.object(printer_manager, "get_normalized_state", return_value={
        "fan_model": 80, "fan_aux": 60, "fan_box": 40,
    }):
        resp = await client.post(
            f"/api/v1/printers/{printer_id}/fan",
            json={"fan": "auxiliary", "speed_pct": 0},
        )
    assert resp.status_code == 200
    mock.set_fan_speeds.assert_called_once_with(80, 0, 40)
    printer_manager._clients.pop(printer_id)


async def test_fan_ok_changes_box_fan(client):
    printer_id = await _create_printer(client)
    mock = _mock_connected_client()
    printer_manager._clients[printer_id] = mock
    with patch.object(printer_manager, "get_normalized_state", return_value={
        "fan_model": 80, "fan_aux": 60, "fan_box": 40,
    }):
        resp = await client.post(
            f"/api/v1/printers/{printer_id}/fan",
            json={"fan": "box", "speed_pct": 100},
        )
    assert resp.status_code == 200
    mock.set_fan_speeds.assert_called_once_with(80, 60, 100)
    printer_manager._clients.pop(printer_id)


async def test_fan_422_on_invalid_fan_name(client):
    printer_id = await _create_printer(client)
    mock = _mock_connected_client()
    printer_manager._clients[printer_id] = mock
    with patch.object(printer_manager, "get_normalized_state", return_value={
        "fan_model": 0, "fan_aux": 0, "fan_box": 0,
    }):
        resp = await client.post(
            f"/api/v1/printers/{printer_id}/fan",
            json={"fan": "turbo", "speed_pct": 100},
        )
    assert resp.status_code == 422
    printer_manager._clients.pop(printer_id)


# ── Bed temp ─────────────────────────────────────────────────────────────────

async def test_bed_temp_ok(client):
    printer_id = await _create_printer(client)
    mock = _mock_connected_client()
    printer_manager._clients[printer_id] = mock
    resp = await client.post(f"/api/v1/printers/{printer_id}/bed-temp", json={"celsius": 95})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    mock.set_bed_temp.assert_called_once_with(95)
    printer_manager._clients.pop(printer_id)


async def test_bed_temp_zero_turns_off(client):
    printer_id = await _create_printer(client)
    mock = _mock_connected_client()
    printer_manager._clients[printer_id] = mock
    resp = await client.post(f"/api/v1/printers/{printer_id}/bed-temp", json={"celsius": 0})
    assert resp.status_code == 200
    mock.set_bed_temp.assert_called_once_with(0)
    printer_manager._clients.pop(printer_id)
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd backend && pytest tests/api/test_printer_controls.py -v
```
Expected: FAIL — 404 on all routes (they don't exist yet).

- [ ] **Step 3: Add the 7 endpoints to printers.py**

In `backend/app/api/routes/printers.py`, add these request body models after the existing `ActivePresetUpdate` class:

```python
class LightBody(BaseModel):
    on: bool


class JogZBody(BaseModel):
    distance_mm: float


class FanBody(BaseModel):
    fan: str  # "model" | "auxiliary" | "box"
    speed_pct: int


class BedTempBody(BaseModel):
    celsius: int
```

Add a helper function after `_get_or_404`:

```python
def _get_connected_client(printer_id: int):
    client = printer_manager._clients.get(printer_id)
    if client is None or not client.connected:
        raise HTTPException(503, "Printer not connected")
    return client
```

Add the 7 route handlers before the existing `@router.get("/{printer_id}/camera")` route:

```python
@router.post("/{printer_id}/pause")
async def pause_printer(
    printer_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _get_or_404(printer_id, session)
    client = _get_connected_client(printer_id)
    client.pause_print()
    return {"ok": True}


@router.post("/{printer_id}/resume")
async def resume_printer(
    printer_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _get_or_404(printer_id, session)
    client = _get_connected_client(printer_id)
    client.resume_print()
    return {"ok": True}


@router.post("/{printer_id}/stop")
async def stop_printer(
    printer_id: int,
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _get_or_404(printer_id, session)
    client = _get_connected_client(printer_id)
    client.stop_print()
    return {"ok": True}


@router.post("/{printer_id}/light")
async def set_light(
    printer_id: int,
    body: LightBody,
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _get_or_404(printer_id, session)
    client = _get_connected_client(printer_id)
    client.set_chamber_light(body.on)
    return {"ok": True}


@router.post("/{printer_id}/jog-z")
async def jog_z(
    printer_id: int,
    body: JogZBody,
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _get_or_404(printer_id, session)
    client = _get_connected_client(printer_id)
    client.jog_z(body.distance_mm)
    return {"ok": True}


@router.post("/{printer_id}/fan")
async def set_fan(
    printer_id: int,
    body: FanBody,
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _get_or_404(printer_id, session)
    client = _get_connected_client(printer_id)
    state = printer_manager.get_normalized_state(printer_id)
    model = int(state.get("fan_model", 0))
    aux = int(state.get("fan_aux", 0))
    box = int(state.get("fan_box", 0))
    if body.fan == "model":
        model = body.speed_pct
    elif body.fan == "auxiliary":
        aux = body.speed_pct
    elif body.fan == "box":
        box = body.speed_pct
    else:
        raise HTTPException(422, f"Invalid fan name: {body.fan!r}. Valid: model, auxiliary, box")
    client.set_fan_speeds(model, aux, box)
    return {"ok": True}


@router.post("/{printer_id}/bed-temp")
async def set_bed_temp(
    printer_id: int,
    body: BedTempBody,
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _get_or_404(printer_id, session)
    client = _get_connected_client(printer_id)
    client.set_bed_temp(body.celsius)
    return {"ok": True}
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
cd backend && pytest tests/api/test_printer_controls.py -v
```
Expected: All pass.

- [ ] **Step 5: Run full suite**

```bash
cd backend && pytest -v
```
Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/routes/printers.py backend/tests/api/test_printer_controls.py
git commit -m "feat(backend): add pause/resume/stop/light/jog-z/fan/bed-temp REST endpoints"
```

---

## Task 5: Camera route enhancement for Elegoo stream activation + ping keepalive

**Files:**
- Modify: `backend/app/api/routes/printers.py`
- Create: `backend/tests/api/test_camera_stream.py`

- [ ] **Step 1: Write failing tests**

Create `backend/tests/api/test_camera_stream.py`:

```python
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from app.services.printer_manager import printer_manager


async def _create_printer(client) -> int:
    resp = await client.post("/api/v1/printers", json={
        "name": "Test", "printer_type": "elegoo_centauri",
        "connection_config": {"ip_address": "192.168.1.20"},
    })
    return resp.json()["id"]


async def test_camera_404_on_missing_printer(client):
    resp = await client.get("/api/v1/printers/999/camera")
    assert resp.status_code == 404


async def test_camera_503_when_not_connected(client):
    printer_id = await _create_printer(client)
    resp = await client.get(f"/api/v1/printers/{printer_id}/camera")
    assert resp.status_code == 503


async def test_camera_404_when_no_camera_capability(client):
    printer_id = await _create_printer(client)
    mock = MagicMock()
    mock.connected = True
    mock.get_capabilities.return_value = MagicMock(camera=False)
    printer_manager._clients[printer_id] = mock
    resp = await client.get(f"/api/v1/printers/{printer_id}/camera")
    assert resp.status_code == 404
    printer_manager._clients.pop(printer_id)


async def test_camera_calls_start_video_stream_when_available(client):
    printer_id = await _create_printer(client)
    mock = MagicMock()
    mock.connected = True
    mock.get_capabilities.return_value = MagicMock(camera=True)
    mock.camera_mjpeg_url = "http://192.168.1.20:3031/video"
    mock.camera_rtsp_url = None

    async def _empty_stream(url):
        return
        yield  # make it an async generator

    printer_manager._clients[printer_id] = mock
    with patch("app.api.routes.printers.stream_mjpeg", _empty_stream):
        resp = await client.get(f"/api/v1/printers/{printer_id}/camera")

    mock.start_video_stream.assert_called_once()
    printer_manager._clients.pop(printer_id)


async def test_camera_skips_start_video_stream_when_not_available(client):
    printer_id = await _create_printer(client)

    class NoVideoClient(MagicMock):
        pass

    mock = NoVideoClient()
    mock.connected = True
    mock.get_capabilities.return_value = MagicMock(camera=True)
    mock.camera_mjpeg_url = "http://192.168.1.20:3031/video"
    mock.camera_rtsp_url = None
    # Remove start_video_stream so hasattr returns False
    del mock.start_video_stream

    async def _empty_stream(url):
        return
        yield

    printer_manager._clients[printer_id] = mock
    with patch("app.api.routes.printers.stream_mjpeg", _empty_stream):
        resp = await client.get(f"/api/v1/printers/{printer_id}/camera")
    # Just verify no error — the hasattr guard worked
    assert resp.status_code in (200, 503)  # 200 if stream started, 503 if mock closed
    printer_manager._clients.pop(printer_id)
```

- [ ] **Step 2: Run tests to confirm baseline**

```bash
cd backend && pytest tests/api/test_camera_stream.py -v
```
Expected: `test_camera_calls_start_video_stream_when_available` FAILS (start_video_stream not called). Others may pass.

- [ ] **Step 3: Enhance the camera route**

In `backend/app/api/routes/printers.py`, add `import asyncio` at the top (it's already imported — verify with grep). Then replace the entire `stream_camera` route:

```python
@router.get("/{printer_id}/camera")
async def stream_camera(
    printer_id: int,
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    await _get_or_404(printer_id, session)
    client = printer_manager._clients.get(printer_id)
    if client is None or not client.connected:
        raise HTTPException(503, "Printer not connected")
    caps = client.get_capabilities()
    if not caps.camera:
        raise HTTPException(404, "This printer has no camera")

    # Activate Elegoo MJPEG stream (no-op for clients that don't implement it)
    if hasattr(client, "start_video_stream"):
        client.start_video_stream()

    if client.camera_mjpeg_url:
        raw = stream_mjpeg(client.camera_mjpeg_url)
    elif client.camera_rtsp_url:
        from ...config import get_ffmpeg_executable
        if not shutil.which(get_ffmpeg_executable()):
            raise HTTPException(503, "ffmpeg not available for RTSP streaming")
        raw = stream_rtsp_ffmpeg(client.camera_rtsp_url)
    else:
        raise HTTPException(404, "No camera URL configured")

    # Ping keepalive: Elegoo drops the MJPEG stream after 60 s of silence.
    # Ping every 45 s while the streaming response is open.
    stop = asyncio.Event()

    async def _ping_loop():
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=45)
            except asyncio.TimeoutError:
                if hasattr(client, "ping_video_stream"):
                    client.ping_video_stream()

    ping_task = asyncio.create_task(_ping_loop())

    async def _stream():
        try:
            async for chunk in raw:
                yield chunk
        finally:
            stop.set()
            ping_task.cancel()

    return StreamingResponse(
        _stream(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
cd backend && pytest tests/api/test_camera_stream.py -v
```
Expected: All pass.

- [ ] **Step 5: Run full suite**

```bash
cd backend && pytest -v
```
Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/routes/printers.py backend/tests/api/test_camera_stream.py
git commit -m "feat(backend): activate Elegoo camera stream and add ping keepalive in camera proxy route"
```

---

## Task 6: Frontend type + FleetPrinter interface + toFleetPrinter mapper

**Files:**
- Modify: `frontend/src/data/types.ts`
- Modify: `frontend/src/api/fleet.ts`
- Test: `frontend/src/api/fleet.test.ts`

- [ ] **Step 1: Write failing tests**

Add to `frontend/src/api/fleet.test.ts`:

```typescript
  it('maps fan_model, fan_aux, fan_box to Printer', () => {
    const p = toFleetPrinter({
      ...BASE,
      fan_model: 80,
      fan_aux: 60,
      fan_box: 40,
    } as any);
    expect(p.fanModel).toBe(80);
    expect(p.fanAux).toBe(60);
    expect(p.fanBox).toBe(40);
  });

  it('defaults fan fields to 0 when absent', () => {
    const p = toFleetPrinter(BASE);
    expect(p.fanModel).toBe(0);
    expect(p.fanAux).toBe(0);
    expect(p.fanBox).toBe(0);
  });

  it('maps temperatures.bed_target to bedTempTarget', () => {
    const p = toFleetPrinter({
      ...BASE,
      temperatures: { nozzle: 285, bed: 95, bed_target: 100 },
    } as any);
    expect(p.bedTempTarget).toBe(100);
  });

  it('defaults bedTempTarget to 0 when bed_target absent', () => {
    const p = toFleetPrinter(BASE);
    expect(p.bedTempTarget).toBe(0);
  });
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd frontend && npx vitest run src/api/fleet.test.ts
```
Expected: FAIL — `fanModel`, `bedTempTarget` properties don't exist.

- [ ] **Step 3: Add fields to Printer type**

In `frontend/src/data/types.ts`, find:
```typescript
  currentJobId: string | null;
  accent: string;
  note?: string;
```
Replace with:
```typescript
  currentJobId: string | null;
  accent: string;
  note?: string;
  fanModel: number;
  fanAux: number;
  fanBox: number;
  bedTempTarget: number;
```

- [ ] **Step 4: Add fields to FleetPrinter interface**

In `frontend/src/api/fleet.ts`, find:
```typescript
  temperatures: { nozzle?: number; bed?: number; chamber?: number };
```
Replace with:
```typescript
  temperatures: { nozzle?: number; bed?: number; chamber?: number; bed_target?: number };
```

Find:
```typescript
  capabilities: Record<string, boolean>;
  current_print: string | null;
```
Replace with:
```typescript
  capabilities: Record<string, boolean>;
  current_print: string | null;
  fan_model: number;
  fan_aux: number;
  fan_box: number;
```

- [ ] **Step 5: Update toFleetPrinter mapper**

In `frontend/src/api/fleet.ts`, find:
```typescript
    currentJobId: p.current_print ?? null,
    accent: ACCENT[p.printer_type] ?? '#888888',
  };
```
Replace with:
```typescript
    currentJobId: p.current_print ?? null,
    accent: ACCENT[p.printer_type] ?? '#888888',
    fanModel: p.fan_model ?? 0,
    fanAux: p.fan_aux ?? 0,
    fanBox: p.fan_box ?? 0,
    bedTempTarget: p.temperatures?.bed_target ?? 0,
  };
```

- [ ] **Step 6: Run tests to confirm they pass**

```bash
cd frontend && npx vitest run src/api/fleet.test.ts
```
Expected: All pass.

- [ ] **Step 7: Run full frontend test suite**

```bash
cd frontend && npx vitest run
```
Expected: All pass.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/data/types.ts frontend/src/api/fleet.ts frontend/src/api/fleet.test.ts
git commit -m "feat(frontend): add fanModel/fanAux/fanBox/bedTempTarget to Printer type and FleetPrinter mapper"
```

---

## Task 7: 7 new API functions in printers.ts + unit tests

**Files:**
- Modify: `frontend/src/api/printers.ts`
- Create: `frontend/src/api/printers.test.ts`

- [ ] **Step 1: Write failing tests**

Create `frontend/src/api/printers.test.ts`:

```typescript
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  pausePrinter,
  resumePrinter,
  stopPrinter,
  setLight,
  jogZ,
  setFanSpeed,
  setBedTemp,
} from './printers';

function mockOkFetch() {
  vi.stubGlobal('fetch', vi.fn(() =>
    Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ ok: true }) }),
  ));
}

function stubFetch() { return vi.mocked(fetch); }

beforeEach(() => mockOkFetch());
afterEach(() => vi.unstubAllGlobals());

describe('pausePrinter', () => {
  it('POSTs to /api/v1/printers/{id}/pause', async () => {
    await pausePrinter('5');
    expect(stubFetch()).toHaveBeenCalledWith(
      '/api/v1/printers/5/pause',
      expect.objectContaining({ method: 'POST' }),
    );
  });

  it('throws on non-OK response', async () => {
    vi.stubGlobal('fetch', vi.fn(() =>
      Promise.resolve({ ok: false, status: 503, text: () => Promise.resolve('Not connected') }),
    ));
    await expect(pausePrinter('5')).rejects.toThrow('503');
  });
});

describe('resumePrinter', () => {
  it('POSTs to /api/v1/printers/{id}/resume', async () => {
    await resumePrinter('7');
    expect(stubFetch()).toHaveBeenCalledWith(
      '/api/v1/printers/7/resume',
      expect.objectContaining({ method: 'POST' }),
    );
  });
});

describe('stopPrinter', () => {
  it('POSTs to /api/v1/printers/{id}/stop', async () => {
    await stopPrinter('3');
    expect(stubFetch()).toHaveBeenCalledWith(
      '/api/v1/printers/3/stop',
      expect.objectContaining({ method: 'POST' }),
    );
  });
});

describe('setLight', () => {
  it('POSTs to /api/v1/printers/{id}/light with on:true', async () => {
    await setLight('1', true);
    expect(stubFetch()).toHaveBeenCalledWith(
      '/api/v1/printers/1/light',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ on: true }),
      }),
    );
  });

  it('POSTs with on:false', async () => {
    await setLight('1', false);
    const [, init] = stubFetch().mock.calls[0];
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({ on: false });
  });
});

describe('jogZ', () => {
  it('POSTs to /api/v1/printers/{id}/jog-z with distance_mm', async () => {
    await jogZ('2', 10);
    expect(stubFetch()).toHaveBeenCalledWith(
      '/api/v1/printers/2/jog-z',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ distance_mm: 10 }),
      }),
    );
  });

  it('sends negative distance for downward jog', async () => {
    await jogZ('2', -10);
    const [, init] = stubFetch().mock.calls[0];
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({ distance_mm: -10 });
  });
});

describe('setFanSpeed', () => {
  it('POSTs to /api/v1/printers/{id}/fan with fan and speed_pct', async () => {
    await setFanSpeed('4', 'model', 80);
    expect(stubFetch()).toHaveBeenCalledWith(
      '/api/v1/printers/4/fan',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ fan: 'model', speed_pct: 80 }),
      }),
    );
  });

  it('sends auxiliary fan', async () => {
    await setFanSpeed('4', 'auxiliary', 60);
    const [, init] = stubFetch().mock.calls[0];
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({ fan: 'auxiliary', speed_pct: 60 });
  });

  it('sends box fan', async () => {
    await setFanSpeed('4', 'box', 40);
    const [, init] = stubFetch().mock.calls[0];
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({ fan: 'box', speed_pct: 40 });
  });
});

describe('setBedTemp', () => {
  it('POSTs to /api/v1/printers/{id}/bed-temp with celsius', async () => {
    await setBedTemp('6', 95);
    expect(stubFetch()).toHaveBeenCalledWith(
      '/api/v1/printers/6/bed-temp',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ celsius: 95 }),
      }),
    );
  });

  it('sends 0 for off', async () => {
    await setBedTemp('6', 0);
    const [, init] = stubFetch().mock.calls[0];
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({ celsius: 0 });
  });
});
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd frontend && npx vitest run src/api/printers.test.ts
```
Expected: FAIL — functions not exported.

- [ ] **Step 3: Add the 7 API functions to printers.ts**

In `frontend/src/api/printers.ts`, add after the `testConnection` function:

```typescript
export function pausePrinter(id: string): Promise<void> {
  return request(`${BASE}/${id}/pause`, { method: 'POST' });
}

export function resumePrinter(id: string): Promise<void> {
  return request(`${BASE}/${id}/resume`, { method: 'POST' });
}

export function stopPrinter(id: string): Promise<void> {
  return request(`${BASE}/${id}/stop`, { method: 'POST' });
}

export function setLight(id: string, on: boolean): Promise<void> {
  return request(`${BASE}/${id}/light`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ on }),
  });
}

export function jogZ(id: string, distanceMm: number): Promise<void> {
  return request(`${BASE}/${id}/jog-z`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ distance_mm: distanceMm }),
  });
}

export function setFanSpeed(
  id: string,
  fan: 'model' | 'auxiliary' | 'box',
  speedPct: number,
): Promise<void> {
  return request(`${BASE}/${id}/fan`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ fan, speed_pct: speedPct }),
  });
}

export function setBedTemp(id: string, celsius: number): Promise<void> {
  return request(`${BASE}/${id}/bed-temp`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ celsius }),
  });
}
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
cd frontend && npx vitest run src/api/printers.test.ts
```
Expected: All pass.

- [ ] **Step 5: Run full frontend suite**

```bash
cd frontend && npx vitest run
```
Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api/printers.ts frontend/src/api/printers.test.ts
git commit -m "feat(frontend): add pausePrinter, resumePrinter, stopPrinter, setLight, jogZ, setFanSpeed, setBedTemp API functions"
```

---

## Task 8: VideoTile camera wiring + error fallback

**Files:**
- Modify: `frontend/src/components/ui.tsx`
- Modify: `frontend/src/components/ui.test.tsx`
- Modify: `frontend/src/screens/FleetScreen.tsx`

- [ ] **Step 1: Write failing tests**

In `frontend/src/components/ui.test.tsx`, add:

```typescript
import { render, screen } from '@testing-library/react';
import { VideoTile } from './ui';

describe('VideoTile', () => {
  it('renders img with camera URL when printerId is set and live is true', () => {
    render(<VideoTile live={true} printerId="42" />);
    const img = document.querySelector('img');
    expect(img).toBeTruthy();
    expect(img!.src).toContain('/api/v1/printers/42/camera');
  });

  it('does not render img when live is false even with printerId', () => {
    render(<VideoTile live={false} printerId="42" />);
    expect(document.querySelector('img')).toBeNull();
  });

  it('does not render img when printerId is absent', () => {
    render(<VideoTile live={true} />);
    expect(document.querySelector('img')).toBeNull();
  });

  it('falls back to placeholder when img fires onError', () => {
    render(<VideoTile live={true} printerId="42" />);
    const img = document.querySelector('img')!;
    // Trigger error
    img.dispatchEvent(new Event('error'));
    expect(document.querySelector('img')).toBeNull();
  });
});
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd frontend && npx vitest run src/components/ui.test.tsx
```
Expected: FAIL — VideoTile doesn't accept `printerId`, no `<img>` rendered.

- [ ] **Step 3: Update VideoTile in ui.tsx**

In `frontend/src/components/ui.tsx`, find:
```typescript
export function VideoTile({ live = true, status, time }: { live?: boolean; status?: StatusKey; time?: number }) {
  return (
    <div className={`video ${live ? 'live' : ''}`}>
      <div className="feed-scene" />
      <div className="feed-noise" />
      {time != null && <div className="feed-time mono">{fmtClock(time)}</div>}
      {status && (
        <div style={{ position: 'absolute', top: 8, right: 8, zIndex: 3 }}>
          <StatusPill status={status} />
        </div>
      )}
    </div>
  );
}
```
Replace with:
```typescript
export function VideoTile({
  live = true,
  status,
  time,
  printerId,
}: {
  live?: boolean;
  status?: StatusKey;
  time?: number;
  printerId?: string;
}) {
  const [imgError, setImgError] = React.useState(false);
  const showCamera = live && printerId && !imgError;
  return (
    <div className={`video ${live ? 'live' : ''}`}>
      {showCamera ? (
        <img
          src={`/api/v1/printers/${printerId}/camera`}
          style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }}
          onError={() => setImgError(true)}
          alt=""
        />
      ) : (
        <>
          <div className="feed-scene" />
          <div className="feed-noise" />
        </>
      )}
      {time != null && <div className="feed-time mono">{fmtClock(time)}</div>}
      {status && (
        <div style={{ position: 'absolute', top: 8, right: 8, zIndex: 3 }}>
          <StatusPill status={status} />
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
cd frontend && npx vitest run src/components/ui.test.tsx
```
Expected: All pass.

- [ ] **Step 5: Pass printerId to VideoTile in PrinterExpandedCard**

In `frontend/src/screens/FleetScreen.tsx`, find inside `PrinterExpandedCard`:
```tsx
          <VideoTile live={isPrinting} time={p.timeElapsed} />
          <div style={{ fontSize: 11, color: 'var(--text-3)', marginTop: -8 }}>
            Live camera — stub (see GitHub issues for wiring)
          </div>
```
Replace with:
```tsx
          <VideoTile live={isPrinting} time={p.timeElapsed} printerId={p.id} />
```

- [ ] **Step 6: Run full suite**

```bash
cd frontend && npx vitest run
```
Expected: All pass.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/ui.tsx frontend/src/components/ui.test.tsx frontend/src/screens/FleetScreen.tsx
git commit -m "feat(frontend): wire VideoTile to live camera feed with error fallback"
```

---

## Task 9: PrinterExpandedCard controls UI + FleetScreen tests

**Files:**
- Modify: `frontend/src/screens/FleetScreen.tsx`
- Modify: `frontend/src/screens/FleetScreen.test.tsx`

**Key capability string checks** (after `_/g → ' '` transform in `toFleetPrinter`):
- `p.capabilities.includes('chamber light')` → shows light toggle
- `p.capabilities.includes('pause resume')` → shows Z-jog buttons
- `p.capabilities.includes('fan control')` → shows Fans card
- `p.capabilities.includes('temp control')` → shows Bed temp card

- [ ] **Step 1: Write failing tests**

Add to `frontend/src/screens/FleetScreen.test.tsx`:

```typescript
import { fireEvent } from '@testing-library/react';
import * as printersApi from '../api/printers';

// ── Fixtures ────────────────────────────────────────────────────────────────
const PRINTER_CONTROLS: FleetPrinter = {
  ...PRINTER_1,
  state: 'RUNNING',
  capabilities: {
    pause_resume: true,
    chamber_light: true,
    fan_control: true,
    temp_control: true,
  },
  fan_model: 80,
  fan_aux: 60,
  fan_box: 40,
};

// ── Pause button ─────────────────────────────────────────────────────────────

it('clicking Pause calls pausePrinter with printer id', async () => {
  const spy = vi.spyOn(printersApi, 'pausePrinter').mockResolvedValue(undefined);
  vi.stubGlobal('WebSocket', MockWS);
  mockFetch([PRINTER_CONTROLS]);

  const { findByText } = render(<FleetScreen />);
  fireEvent.click(await findByText('Forge'));
  fireEvent.click(await findByText('Pause'));
  expect(spy).toHaveBeenCalledWith(String(PRINTER_CONTROLS.id));
  spy.mockRestore();
});

// ── Resume button ────────────────────────────────────────────────────────────

it('clicking Resume calls resumePrinter', async () => {
  const spy = vi.spyOn(printersApi, 'resumePrinter').mockResolvedValue(undefined);
  const pausedPrinter: FleetPrinter = { ...PRINTER_CONTROLS, state: 'PAUSE' };
  mockFetch([pausedPrinter]);

  const { findByText } = render(<FleetScreen />);
  fireEvent.click(await findByText('Forge'));
  fireEvent.click(await findByText('Resume'));
  expect(spy).toHaveBeenCalledWith(String(PRINTER_CONTROLS.id));
  spy.mockRestore();
});

// ── Stop button ──────────────────────────────────────────────────────────────

it('clicking Stop calls stopPrinter', async () => {
  const spy = vi.spyOn(printersApi, 'stopPrinter').mockResolvedValue(undefined);
  mockFetch([PRINTER_CONTROLS]);

  const { findByText } = render(<FleetScreen />);
  fireEvent.click(await findByText('Forge'));
  fireEvent.click(await findByText('Stop'));
  expect(spy).toHaveBeenCalledWith(String(PRINTER_CONTROLS.id));
  spy.mockRestore();
});

// ── Chamber light toggle ─────────────────────────────────────────────────────

it('clicking chamber light toggle calls setLight', async () => {
  const spy = vi.spyOn(printersApi, 'setLight').mockResolvedValue(undefined);
  mockFetch([PRINTER_CONTROLS]);

  const { findByTitle } = render(<FleetScreen />);
  fireEvent.click(await screen.findByText('Forge'));
  fireEvent.click(await findByTitle('Toggle chamber light'));
  expect(spy).toHaveBeenCalledWith(String(PRINTER_CONTROLS.id), true);
  spy.mockRestore();
});

// ── Z-jog ────────────────────────────────────────────────────────────────────

it('clicking +10mm calls jogZ with +10', async () => {
  const spy = vi.spyOn(printersApi, 'jogZ').mockResolvedValue(undefined);
  mockFetch([PRINTER_CONTROLS]);

  const { findByText } = render(<FleetScreen />);
  fireEvent.click(await findByText('Forge'));
  fireEvent.click(await findByText('+10 mm'));
  expect(spy).toHaveBeenCalledWith(String(PRINTER_CONTROLS.id), 10);
  spy.mockRestore();
});

it('clicking −10mm calls jogZ with -10', async () => {
  const spy = vi.spyOn(printersApi, 'jogZ').mockResolvedValue(undefined);
  mockFetch([PRINTER_CONTROLS]);

  const { findByText } = render(<FleetScreen />);
  fireEvent.click(await findByText('Forge'));
  fireEvent.click(await findByText('−10 mm'));
  expect(spy).toHaveBeenCalledWith(String(PRINTER_CONTROLS.id), -10);
  spy.mockRestore();
});
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd frontend && npx vitest run src/screens/FleetScreen.test.tsx
```
Expected: Multiple FAILs — buttons don't exist yet.

- [ ] **Step 3: Add control imports to FleetScreen.tsx**

At the top of `frontend/src/screens/FleetScreen.tsx`, add imports. Find:
```typescript
import React, { useState } from 'react';
```
Replace with:
```typescript
import React, { useState, useRef } from 'react';
```

After the existing import block (before the `type Layout = ...` line), add:
```typescript
import {
  pausePrinter,
  resumePrinter,
  stopPrinter,
  setLight,
  jogZ,
  setFanSpeed,
  setBedTemp,
} from '../api/printers';
```

- [ ] **Step 4: Wire action row in PrinterExpandedCard**

In `FleetScreen.tsx`, add state hooks at the top of `PrinterExpandedCard` (inside the function, before any JSX). Find:
```typescript
  const isPrinting = p.status === 'printing';
  const job: Job | undefined = p.currentJobId
```
Replace with:
```typescript
  const isPrinting = p.status === 'printing';
  const isPaused = p.status === 'paused';
  const isOffline = p.status === 'offline';
  const [lightOn, setLightOn] = useState(false);
  const [fanValues, setFanValues] = useState({
    model: p.fanModel,
    auxiliary: p.fanAux,
    box: p.fanBox,
  });
  const [bedInput, setBedInput] = useState(String(p.bedTempTarget));
  const fanDebounceRef = useRef<Record<string, ReturnType<typeof setTimeout>>>({});
  const job: Job | undefined = p.currentJobId
```

- [ ] **Step 5: Replace the action row**

Find the existing action row:
```tsx
          {/* Action row */}
          <div className="row gap-2" style={{ marginTop: 2 }}>
            {isPrinting && (
              <>
                <button className="btn">{Icons.pause} Pause</button>
                <button className="btn">{Icons.stop} Stop</button>
              </>
            )}
            {!isPrinting && (
              <button className="btn primary">
                {Icons.play} Claim next from queue
              </button>
            )}
            <div style={{ flex: 1 }} />
            <button className="btn ghost sm">
              {Icons.external} Open in slicer
            </button>
          </div>
```
Replace with:
```tsx
          {/* Action row */}
          <div className="row gap-2" style={{ marginTop: 2, flexWrap: 'wrap' }}>
            {isPrinting && (
              <button
                className="btn"
                onClick={() => pausePrinter(p.id).catch(console.error)}
              >
                {Icons.pause} Pause
              </button>
            )}
            {isPaused && (
              <button
                className="btn"
                onClick={() => resumePrinter(p.id).catch(console.error)}
              >
                {Icons.play} Resume
              </button>
            )}
            {(isPrinting || isPaused) && (
              <button
                className="btn"
                onClick={() => stopPrinter(p.id).catch(console.error)}
              >
                {Icons.stop} Stop
              </button>
            )}
            {!isPrinting && !isPaused && (
              <button className="btn primary">
                {Icons.play} Claim next from queue
              </button>
            )}
            {p.capabilities.includes('chamber light') && (
              <button
                className={`btn sm${lightOn ? ' active' : ''}`}
                title="Toggle chamber light"
                onClick={() => {
                  const next = !lightOn;
                  setLightOn(next);
                  setLight(p.id, next).catch(console.error);
                }}
              >
                {lightOn ? 'Light: On' : 'Light: Off'}
              </button>
            )}
            {p.capabilities.includes('pause resume') && (
              <>
                <button
                  className="btn sm"
                  onClick={() => jogZ(p.id, 10).catch(console.error)}
                >
                  +10 mm
                </button>
                <button
                  className="btn sm"
                  onClick={() => jogZ(p.id, -10).catch(console.error)}
                >
                  −10 mm
                </button>
              </>
            )}
            <div style={{ flex: 1 }} />
            <button className="btn ghost sm">
              {Icons.external} Open in slicer
            </button>
          </div>
```

- [ ] **Step 6: Add Fans card to the right column**

In `FleetScreen.tsx`, find the right column contents. Find:
```tsx
          <div
            className="card"
            style={{ padding: 14, background: 'var(--bg-1)' }}
          >
            <div
              className="row between"
              style={{ marginBottom: 10 }}
            >
              <span className="tag-key">Queue eligibility</span>
```

Add the Fans card and Bed temp card before this Queue eligibility card:
```tsx
          {p.capabilities.includes('fan control') && (
            <div className="card" style={{ padding: 14, background: 'var(--bg-1)' }}>
              <div className="tag-key" style={{ marginBottom: 10 }}>Fans</div>
              <div className="col gap-3">
                {(['model', 'auxiliary', 'box'] as const).map(fan => (
                  <div key={fan} className="col gap-1">
                    <div className="row between">
                      <span className="small" style={{ textTransform: 'capitalize' }}>{fan}</span>
                      <span className="num small muted">{fanValues[fan]}%</span>
                    </div>
                    <input
                      type="range"
                      min={0}
                      max={100}
                      value={fanValues[fan]}
                      disabled={isOffline}
                      style={{ width: '100%' }}
                      onChange={e => {
                        const value = Number(e.target.value);
                        setFanValues(prev => ({ ...prev, [fan]: value }));
                        clearTimeout(fanDebounceRef.current[fan]);
                        fanDebounceRef.current[fan] = setTimeout(() => {
                          setFanSpeed(p.id, fan, value).catch(console.error);
                        }, 300);
                      }}
                    />
                  </div>
                ))}
              </div>
            </div>
          )}

          {p.capabilities.includes('temp control') && (
            <div className="card" style={{ padding: 14, background: 'var(--bg-1)' }}>
              <div className="row between" style={{ marginBottom: 10 }}>
                <span className="tag-key">Bed Temp</span>
                <span className="num small muted">
                  target: {p.bedTempTarget}°C
                </span>
              </div>
              <div className="row gap-2" style={{ flexWrap: 'wrap', marginBottom: 10 }}>
                {[0, 60, 80, 95, 110].map(preset => (
                  <button
                    key={preset}
                    className="btn sm"
                    disabled={isOffline}
                    onClick={() => {
                      setBedInput(String(preset));
                      setBedTemp(p.id, preset).catch(console.error);
                    }}
                  >
                    {preset === 0 ? 'Off' : `${preset}°C`}
                  </button>
                ))}
              </div>
              <div className="row gap-2">
                <input
                  type="number"
                  min={0}
                  max={120}
                  value={bedInput}
                  disabled={isOffline}
                  style={{ width: 72, padding: '4px 8px', borderRadius: 6,
                    background: 'var(--bg-3)', border: '1px solid var(--border-1)',
                    color: 'var(--text-1)', fontSize: 13 }}
                  onChange={e => setBedInput(e.target.value)}
                />
                <button
                  className="btn sm"
                  disabled={isOffline}
                  onClick={() => {
                    const c = parseInt(bedInput, 10);
                    if (!isNaN(c)) setBedTemp(p.id, c).catch(console.error);
                  }}
                >
                  Set
                </button>
              </div>
            </div>
          )}

```

- [ ] **Step 7: Run FleetScreen tests**

```bash
cd frontend && npx vitest run src/screens/FleetScreen.test.tsx
```
Expected: All pass. Debug any failures by reading the test output carefully — common issues are render timing (use `findBy` not `getBy`) or capability string mismatches.

- [ ] **Step 8: Run full suite**

```bash
cd frontend && npx vitest run
```
Expected: All pass.

- [ ] **Step 9: Commit**

```bash
git add frontend/src/screens/FleetScreen.tsx frontend/src/screens/FleetScreen.test.tsx
git commit -m "feat(frontend): wire PrinterExpandedCard controls — pause/resume/stop, light, Z-jog, fans card, bed temp card"
```

---

## Task 10: Open PR

- [ ] **Step 1: Run all backend tests one final time**

```bash
cd backend && pytest -v
```
Expected: All pass.

- [ ] **Step 2: Run all frontend tests one final time**

```bash
cd frontend && npx vitest run
```
Expected: All pass.

- [ ] **Step 3: Push branch**

```bash
git push -u origin feat/fleet-camera-controls
```

- [ ] **Step 4: Open PR**

```bash
gh pr create \
  --title "feat: fleet camera feed and printer controls" \
  --base main \
  --body "$(cat <<'EOF'
## Summary
- Wires the `VideoTile` camera feed to `/api/v1/printers/{id}/camera` with error fallback to placeholder
- Enhances the camera proxy route to activate the Elegoo MJPEG stream (Cmd 386) and send 45 s ping keepalives
- Adds 7 REST control endpoints: pause, resume, stop, light, jog-z, fan, bed-temp
- Adds `set_fan_speeds` and `set_bed_temp` to the ABC and Elegoo client (Cmd 403)
- Adds `fan_box` field to `ElegooState` and exposes all three fan fields in the normalized state dict
- Adds `fan_control` and `temp_control` capability flags
- Extends `PrinterExpandedCard` with wired action row buttons, chamber light toggle, Z-jog, fans sliders (debounced), and bed temp card with presets

## Test plan
- [ ] Deploy locally (`docker compose up --build`) and open Fleet screen
- [ ] Expand a printer card — verify camera feed appears (not the placeholder) when printer is printing
- [ ] Click Pause while printing — verify printer pauses; Resume button appears
- [ ] Click Stop while printing — verify print cancels
- [ ] Toggle chamber light — verify light responds on printer
- [ ] Click +10 mm / −10 mm — verify plate moves
- [ ] Drag a fan slider — verify fan speed changes after 300 ms debounce
- [ ] Click bed temp preset (e.g. 95°C) — verify bed heats; click Off — verify it stops
- [ ] Disable a printer network connection and verify fan sliders + bed temp input are grayed out
- [ ] Disconnect printer — verify camera falls back to placeholder

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```
