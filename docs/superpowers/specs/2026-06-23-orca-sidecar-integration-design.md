# Orca Sidecar Integration — Design Spec

**Date:** 2026-06-23  
**Branches:** `themis:orca-sidecar`, `orca:feat/themis-integration`  
**Status:** Implemented (see `docs/slicing-flow.md` for current architecture)

> **Note:** This spec describes the initial design. The implementation diverged in one key way: Themis sends profile UUIDs to the sidecar (not a pre-built prepared 3MF), and there is no local-binary fallback. The sidecar is the sole source of truth for profile resolution and slicing.

---

## Goal

Run Themis and Orca as a pair of Docker containers (orchestrated from `themis/docker-compose.yml`) so that every slice job Themis processes goes to Orca as a sidecar instead of calling a local OrcaSlicer binary. Stretch: a new Orca endpoint that accepts N STLs + bed dimensions and returns a multi-plate 3MF, which Themis stores as a library upload.

---

## 1. Compose Architecture

**File:** `themis/docker-compose.yml`

```yaml
services:
  orca:
    build: ../orca
    volumes:
      - ../orca/config:/config
      - ../orca/data:/data
    shm_size: "1gb"
    environment:
      - TZ=Etc/UTC
      - PYTHONUNBUFFERED=1
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:5000/api/health"]
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 60s
    restart: unless-stopped

  themis:
    build: .
    ports:
      - "${HOST_PORT:-8001}:8000"
    volumes:
      - "${APPDATA}/OrcaSlicer:/root/.config/OrcaSlicer:ro"
      - themis-data:/data
    environment:
      - ORCA_SIDECAR_URL=http://orca:5000
      - THEMIS_LIBRARY_DIR=/data/library
    depends_on:
      orca:
        condition: service_healthy
    restart: unless-stopped

volumes:
  themis-data:
```

Key decisions:
- `start_period: 60s` — Orca extracts the AppImage and builds its profile catalog on first boot; health check must not fire until that completes.
- OrcaSlicer config dir mounted **read-only** into Themis (`/root/.config/OrcaSlicer`) — available for tooling that inspects profile JSON. Orca does not need this mount; its system profiles live inside the image at `/opt/orcaslicer/resources/profiles`.
- `themis-data` named volume holds the SQLite DB + uploads between restarts. The `orca/config` and `orca/data` are bind-mounted from the repo so profiles added to the host appear immediately.

---

## 2. Profile Resolution Chain

> **Implemented differently.** Themis sends profile UUIDs to the sidecar; the sidecar resolves them from its catalog. Themis never builds a prepared 3MF locally.

Original design (for reference):

```
Spoolman extra.orca_profiles
    └─► DB: printer.loaded_filaments[slot].filament_profile   (synced on spool load)
            └─► QueueEngine._slot_for_config()
                    └─► SliceRequest.filament_presets[0..N]
                            └─► SlicerService._build_config()
                                    └─► PresetResolver.resolve(name, "machine"|"process"|"filament")
                                            (reads /root/.config/OrcaSlicer JSON, walks inherits)
                                    └─► build_project_config(machine, process, filaments, colours)
                                    └─► build_sliceable_3mf(source, config, prepared.3mf)
                                            (embeds project_settings.config)
                                    └─► prepare_hook(prepared.3mf)   [vendor remap]
                                    └─► _execute_slice(prepared.3mf) → POST /api/slice/prepared
```

---

## 3. Stories

### Story 1 — OrcaSidecarClient ✅

**New file:** `backend/app/services/orca_sidecar_client.py`

Synchronous httpx client (no async — `SlicerService` runs in a `ThreadPoolExecutor`).

### Story 2 — Replace `_execute_slice()` ✅

**File:** `backend/app/services/slicer_service.py`

Implemented as UUID-based sidecar-only slicing via `POST /api/slice/start`. No local fallback.

### Story 3 — Startup Health Check ✅

**File:** `backend/app/main.py` — logs warning if sidecar unreachable at startup.

### Story 4 (Stretch) — `POST /api/pack` on Orca

New endpoint accepting N STLs + bed dimensions, returns a multi-plate 3MF.

### Story 5 (Stretch) — Themis Library Pack

`OrcaSidecarClient.pack_stls()` + `POST /api/v1/files/pack` + library UI multi-select.

---

## 4. Acceptance Criteria

- `docker compose up` in `themis/` starts both containers; Orca passes its healthcheck before Themis starts. ✅
- Each job in the Themis queue that has a test slice configured runs successfully end-to-end. ✅
- Orca's returned gcode/3mf artifacts contain embedded thumbnails. ✅
- `POST /api/pack` with 3 STLs returns a valid multi-plate 3MF parseable by `three_mf_parser`. (stretch — not yet implemented)
- "Pack & Save" flow in Themis UI creates a new library entry. (stretch — not yet implemented)
