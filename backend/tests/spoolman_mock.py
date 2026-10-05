"""Mock Spoolman for E2E testing.

Implements the Spoolman REST API subset used by Themis:
    GET  /api/v1/info
    GET  /api/v1/filament
    GET  /api/v1/filament/{id}
    PATCH /api/v1/filament/{id}
    GET  /api/v1/spool
    GET  /api/v1/spool/{id}
    PATCH /api/v1/spool/{id}
    PUT  /api/v1/spool/{id}/use

Run via docker-compose.test.yml, or standalone:
    uvicorn spoolman_mock:app --host 0.0.0.0 --port 7912
"""
from __future__ import annotations

import copy

from fastapi import FastAPI, HTTPException

app = FastAPI(title="Spoolman Mock", version="mock")

_FILAMENTS: list[dict] = [
    {
        "id": 1,
        "registered": "2024-01-01T00:00:00Z",
        "name": "PLA White",
        "material": "PLA",
        "color_hex": "FFFFFF",
        "density": 1.24,
        "diameter": 1.75,
        "weight": 1000.0,
        "spool_weight": 250.0,
        "vendor": {"id": 1, "registered": "2024-01-01T00:00:00Z", "name": "Elegoo"},
        "extra": {},
    },
    {
        "id": 2,
        "registered": "2024-01-01T00:00:00Z",
        "name": "PLA Black",
        "material": "PLA",
        "color_hex": "000000",
        "density": 1.24,
        "diameter": 1.75,
        "weight": 1000.0,
        "spool_weight": 250.0,
        "vendor": {"id": 1, "registered": "2024-01-01T00:00:00Z", "name": "Elegoo"},
        "extra": {},
    },
]

_SPOOLS: list[dict] = [
    {
        "id": 1,
        "registered": "2024-01-01T00:00:00Z",
        "first_used": None,
        "last_used": None,
        "filament": copy.deepcopy(_FILAMENTS[0]),
        "remaining_weight": 800.0,
        "used_weight": 200.0,
        "location": "Slot 1",
        "lot_nr": None,
        "comment": None,
        "archived": False,
        "extra": {},
    },
    {
        "id": 2,
        "registered": "2024-01-01T00:00:00Z",
        "first_used": None,
        "last_used": None,
        "filament": copy.deepcopy(_FILAMENTS[1]),
        "remaining_weight": 500.0,
        "used_weight": 500.0,
        "location": "Slot 2",
        "lot_nr": None,
        "comment": None,
        "archived": False,
        "extra": {},
    },
]


@app.get("/api/v1/info")
async def info():
    return {"version": "1.0.0-mock", "debug_mode": False}


@app.get("/api/v1/filament")
async def list_filaments():
    return _FILAMENTS


@app.get("/api/v1/filament/{filament_id}")
async def get_filament(filament_id: int):
    for f in _FILAMENTS:
        if f["id"] == filament_id:
            return f
    raise HTTPException(404, f"Filament {filament_id} not found")


@app.patch("/api/v1/filament/{filament_id}")
async def patch_filament(filament_id: int, body: dict):
    for f in _FILAMENTS:
        if f["id"] == filament_id:
            extra = body.get("extra", {})
            f["extra"].update(extra)
            # keep spool copies in sync
            for s in _SPOOLS:
                if s["filament"]["id"] == filament_id:
                    s["filament"]["extra"].update(extra)
            return f
    raise HTTPException(404, f"Filament {filament_id} not found")


@app.get("/api/v1/spool")
async def list_spools():
    return _SPOOLS


def _spool(spool_id: int) -> dict:
    for s in _SPOOLS:
        if s["id"] == spool_id:
            return s
    raise HTTPException(404, f"Spool {spool_id} not found")


@app.get("/api/v1/spool/{spool_id}")
async def get_spool(spool_id: int):
    return _spool(spool_id)


@app.patch("/api/v1/spool/{spool_id}")
async def patch_spool(spool_id: int, body: dict):
    """Spoolman couples the two weights: `remaining_weight` and `used_weight` describe the same quantity
    (remaining = initial - used, initial = the filament's `weight`). Setting either derives the other; sending both
    is a 400. Setting a value never changes it afterwards, and sending it again is a no-op.
    Verified against a real instance by protocol_verification/test_spoolman_weight.py (BIZ-203)."""
    s = _spool(spool_id)
    if "remaining_weight" in body and "used_weight" in body:
        raise HTTPException(400, "Only specify either remaining_weight or used_weight.")
    initial = s["filament"].get("weight")
    if "remaining_weight" in body:
        if initial is None:
            raise HTTPException(400, "remaining_weight can only be used if the filament has a weight set.")
        remaining = max(0.0, float(body["remaining_weight"]))
        s["remaining_weight"], s["used_weight"] = remaining, max(0.0, initial - remaining)
    elif "used_weight" in body:
        used = max(0.0, float(body["used_weight"]))
        s["used_weight"] = used
        if initial is not None:
            s["remaining_weight"] = max(0.0, initial - used)
    for key in ("location", "comment", "archived"):
        if key in body:
            s[key] = body[key]
    return s


@app.put("/api/v1/spool/{spool_id}/use")
async def record_spool_use(spool_id: int, body: dict):
    for s in _SPOOLS:
        if s["id"] == spool_id:
            grams = float(body.get("use_weight", 0))
            s["remaining_weight"] = max(0.0, s["remaining_weight"] - grams)
            s["used_weight"] = s["used_weight"] + grams
            return s
    raise HTTPException(404, f"Spool {spool_id} not found")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7912)
