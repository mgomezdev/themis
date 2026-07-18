"""Mock Spoolman for E2E testing.

Implements the Spoolman REST API subset used by Themis:
    GET  /api/v1/info
    GET  /api/v1/filament
    GET  /api/v1/filament/{id}
    PATCH /api/v1/filament/{id}
    GET  /api/v1/spool
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
