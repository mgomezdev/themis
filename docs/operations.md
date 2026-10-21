# Installation and administration

## Docker Compose deployment

The root `docker-compose.yml` builds two services: Themis and Laminus. The Laminus build context is `../laminus`, so check out that repository beside this one. The Compose file bind-mounts OrcaSlicer system and user profiles from the host and mounts the complete host OrcaSlicer configuration read-only into Themis. Set `APPDATA` in `.env` to the parent OrcaSlicer configuration directory; the supplied `.env.example` documents Windows and Linux paths.

```sh
docker compose up --build
```

Open `http://localhost:8001`. The container listens on port 8000; `HOST_PORT` changes the host-side port (default 8001). The `themis-data` named volume stores the database, uploaded files, cached files, plugins, and other persistent application data. Keep this volume when upgrading. Laminus has its own `../laminus/config` and `../laminus/data` bind mounts.

To run only Laminus for local development, publish its port with the dev overlay:

```sh
docker compose -f docker-compose.yml -f docker-compose.dev.yml up orca
```

## Local development

Requirements: Python 3.11, Node.js 20 (the Docker build uses Node 20), and optionally Docker plus the sibling Laminus checkout for slicing.

```powershell
# Backend
cd backend
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
# Then set Settings -> Laminus -> URL to http://localhost:5000 (the dev overlay publishes it); the shipped default is the compose host http://orca:5000
uvicorn app.main:app --reload --port 8001
```

In another terminal:

```powershell
cd frontend
npm ci
npm run dev
```

Vite serves the app at `http://localhost:5173` and proxies API and WebSocket requests to the backend on port 8001. If Laminus is not running, app startup and non-slicing features remain available, but operations requiring a slice or project generation cannot complete.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `THEMIS_DATA_DIR` | repository `data/` locally; `/data` in the Compose service | Database and application data root. |
| `THEMIS_LIBRARY_DIR` | `<data dir>/library` | Model library directory. |
| `THEMIS_STATIC_DIR` | `frontend/dist` locally; `/frontend/dist` in the image | Built browser application. |
| `LAMINUS_SIDECAR_URL` | unset | **Deprecated.** Read by core (the Laminus plugin never sees the environment), logs a warning once, and is honoured only while no URL is saved in Settings -> Laminus. Precedence: saved Settings URL > this variable > the plugin's shipped default `http://orca:5000` (`backend/app/plugins/laminus/defaults.toml`, the compose `orca` service, so `docker compose up` needs no variable; outside compose with nothing configured the connection test reports *unavailable*, not *unconfigured*, and Laminus can only be switched off by disabling the plugin). A saved URL applies without restart; the connection test reports not configured / unavailable / healthy. |
| `THEMIS_MOCK_PRINTERS` | unset | Dev/test only, **deprecated** the same way (read by core; mapped in `plugins/mock/defaults.toml`): `1` enables the Mock Printer plugin when no saved enabled state exists. The test suite and the `/themis-start` dev launcher set it. |
| `FFMPEG_EXECUTABLE` | `ffmpeg` | Executable used for RTSP camera transcoding. |
| `THEMIS_LOCAL_NETWORKS` | `192.168.0.0/16` | Comma-separated client IP CIDRs allowed keyless local admin access while that setting is enabled. |
| `THEMIS_BOOTSTRAP_KEY` | unset | Optional full-scope recovery key. Keep it secret and remove it when no longer needed. |
| `THEMIS_MAX_CAMERA_STREAMS` | `64` | Maximum concurrent upstream camera streams (minimum 1). |
| `HOST_PORT` | `8001` (Compose interpolation) | Host port published for Themis. |
| `APPDATA` | empty in `.env.example` | Host OrcaSlicer configuration path used by Compose bind mounts. |

The data directory and library directory are created as needed. In Docker, ensure bind-mounted OrcaSlicer profile directories exist and are readable by the container.

## Printer offline limit

`Settings -> Print defaults -> Printer offline limit` (minutes; default 30; 0 = never) is how long a printer may stay offline before Themis considers it dead.
A shorter drop changes nothing, so a flaky network does not keep reprocessing the queue. When a printer is declared dead, the job it was printing is marked failed
(a `job.failed` event, webhook and notification) and goes back to the top of the queue; a job already sliced for it returns to the queue; the printer keeps its
"plate not cleared" state, so you confirm the bed when it is back. **Risk:** the printer may still be printing on its own (only Themis lost contact). It stays
unavailable until its print ends and you clear the plate, but the requeued job can be printed again elsewhere; raise the limit if your network drops for long periods.

## Authentication and account recovery

On first boot, Themis initializes an administrator account. Keyless requests are treated as local admin only when the peer IP is in `THEMIS_LOCAL_NETWORKS` and local login is enabled in the Admin account settings. A presented API key takes precedence over local-network access, so a limited key remains limited on the LAN. The app does not trust forwarded IP headers for this decision; when running behind a reverse proxy or Docker networking, verify the peer IP observed by Themis before relying on local access.

Create staff/integration keys in Settings → API Keys and grant only the scopes needed. Customer sign-ins are restricted to their own customer portal. Set a password and restrict local login before exposing the service beyond a trusted network.

For offline recovery, run `docker compose exec themis python -m app.admin reset-password` or use the one-time recovery flow and read its code from the Themis server log. The admin CLI also supports changing local-login access; run `docker compose exec themis python -m app.admin --help` for available commands.

## Backups and upgrades

Back up the complete Themis data volume, not just `themis.db`: it also contains uploaded library files, caches, and installed plugins. Stop writes or stop the service before taking a filesystem-level backup of the SQLite database and associated files. The schema migration runner applies core migrations during startup; plugin-owned migrations are isolated and plugin failures do not prevent core startup. Preserve a backup before upgrading.

The health endpoint is `GET /api/v1/health`. The OpenAPI UI is `/docs`; the schema is `/openapi.json`.
