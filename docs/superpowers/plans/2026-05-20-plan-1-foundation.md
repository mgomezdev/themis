# Themis – Plan 1: Foundation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Scaffold the full project (backend + frontend), establish the async SQLite database layer with all six ORM models, create a running FastAPI server with a health endpoint, and produce a Docker image that starts cleanly with correct volume mounts.

**Architecture:** Single Docker container. FastAPI (Python 3.11) serves both the API and the built React app as static files. SQLite in WAL mode via async SQLAlchemy + aiosqlite. All six data models are defined in this plan; the routes that use them come in later plans.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy 2.0 (async), aiosqlite, uvicorn, React 18, Vite 5, TypeScript 5, pytest + pytest-asyncio + httpx, Docker (multi-stage)

---

## File Map

### Backend
| File | Responsibility |
|---|---|
| `backend/pyproject.toml` | Package metadata, dependencies, pytest config |
| `backend/app/__init__.py` | Empty package marker |
| `backend/app/main.py` | FastAPI app factory, lifespan hook, health endpoint, static file mount |
| `backend/app/database.py` | Async engine, WAL init, session factory, `Base`, `get_session` dependency |
| `backend/app/models.py` | SQLAlchemy ORM models for all six tables |
| `backend/tests/__init__.py` | Empty package marker |
| `backend/tests/conftest.py` | In-memory SQLite engine, async test client with DB override |
| `backend/tests/test_health.py` | Health endpoint smoke test |
| `backend/tests/test_models.py` | ORM model creation and foreign-key relationship tests |

### Frontend
| File | Responsibility |
|---|---|
| `frontend/package.json` | npm metadata and scripts |
| `frontend/tsconfig.json` | TypeScript compiler config |
| `frontend/vite.config.ts` | Vite config with `/api` proxy to backend :8000 in dev |
| `frontend/index.html` | HTML entry point |
| `frontend/src/main.tsx` | React root mount |
| `frontend/src/App.tsx` | Placeholder page ("Themis is running") |

### Docker & Root
| File | Responsibility |
|---|---|
| `Dockerfile` | Multi-stage: node build stage → Python runtime stage |
| `docker-compose.yml` | Service definition with both volumes |
| `.env.example` | Documents the `APPDATA` variable needed for OrcaSlicer mount |
| `.gitignore` | Node modules, Python cache, SQLite files, dist output |

---

## Task 1: Repo Scaffold & Python Environment

**Files:**
- Create: `backend/pyproject.toml`
- Create: `backend/app/__init__.py`
- Create: `backend/tests/__init__.py`
- Create: `.gitignore`

- [ ] **Step 1: Create directory structure**

```bash
mkdir -p backend/app backend/tests frontend/src
```

- [ ] **Step 2: Write `.gitignore`**

```gitignore
# Python
__pycache__/
*.pyc
.venv/
*.egg-info/
.pytest_cache/

# Frontend
node_modules/
frontend/dist/

# Data
*.db
*.db-wal
*.db-shm
/data/

# Env
.env

# Superpowers
.superpowers/
```

- [ ] **Step 3: Write `backend/pyproject.toml`**

```toml
[project]
name = "themis"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.110.0",
    "uvicorn[standard]>=0.29.0",
    "sqlalchemy>=2.0.0",
    "aiosqlite>=0.20.0",
    "python-multipart>=0.0.9",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",
    "httpx>=0.27.0",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.setuptools.packages.find]
where = ["."]
include = ["app*"]
```

- [ ] **Step 4: Create empty package markers**

```bash
touch backend/app/__init__.py backend/tests/__init__.py
```

- [ ] **Step 5: Create and activate virtual environment**

```bash
cd backend
python -m venv .venv
# Windows:
.venv\Scripts\activate
pip install -e ".[dev]"
```

- [ ] **Step 6: Commit**

```bash
git add backend/pyproject.toml backend/app/__init__.py backend/tests/__init__.py .gitignore
git commit -m "chore: scaffold backend Python package"
```

---

## Task 2: Database Layer

**Files:**
- Create: `backend/app/database.py`
- Create: `backend/app/models.py`
- Create: `backend/tests/test_models.py`

- [ ] **Step 1: Write the failing model tests**

```python
# backend/tests/test_models.py
import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.database import Base
from app.models import Printer, UploadedFile, Project, Job, JobPrinterConfig, GcodeFile


TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture
async def session():
    engine = create_async_engine(TEST_DB_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


async def test_create_printer(session):
    printer = Printer(
        name="X1 Carbon",
        printer_type="bambu",
        connection_config={"serial_number": "ABC123", "access_code": "secret"},
        orca_printer_profiles=["Bambu Lab X1 Carbon 0.4"],
        current_orca_printer_profile="Bambu Lab X1 Carbon 0.4",
    )
    session.add(printer)
    await session.commit()
    await session.refresh(printer)
    assert printer.id is not None
    assert printer.awaiting_plate_clear is False
    assert printer.enabled is True


async def test_create_uploaded_file(session):
    f = UploadedFile(
        original_filename="model.3mf",
        stored_path="/data/uploads/abc/model.3mf",
        plates=[{"plate_number": 1, "thumbnail_path": "/data/uploads/abc/plate_1.png", "estimated_time": 3600, "filament_g": 42.1}],
        uploaded_at="2026-05-20T12:00:00Z",
    )
    session.add(f)
    await session.commit()
    await session.refresh(f)
    assert f.id is not None
    assert len(f.plates) == 1


async def test_create_job_with_printer_config(session):
    printer = Printer(
        name="X1C",
        printer_type="bambu",
        connection_config={},
        orca_printer_profiles=[],
    )
    uploaded_file = UploadedFile(
        original_filename="model.3mf",
        stored_path="/data/uploads/abc/model.3mf",
        plates=[],
        uploaded_at="2026-05-20T12:00:00Z",
    )
    session.add_all([printer, uploaded_file])
    await session.commit()

    job = Job(
        uploaded_file_id=uploaded_file.id,
        plate_number=1,
        queue_position=1.0,
        status="queued",
        created_at="2026-05-20T12:00:00Z",
        updated_at="2026-05-20T12:00:00Z",
    )
    session.add(job)
    await session.commit()

    config = JobPrinterConfig(
        job_id=job.id,
        printer_id=printer.id,
        print_profile="0.20mm Standard @BBL X1C",
        filament_profile="Bambu PLA Basic @BBL X1C",
    )
    session.add(config)
    await session.commit()
    await session.refresh(config)

    assert config.id is not None
    assert config.slice_failed is False
    assert config.slice_error is None


async def test_create_gcode_file(session):
    printer = Printer(name="P", printer_type="bambu", connection_config={}, orca_printer_profiles=[])
    uploaded_file = UploadedFile(original_filename="m.3mf", stored_path="/x", plates=[], uploaded_at="2026-05-20T00:00:00Z")
    session.add_all([printer, uploaded_file])
    await session.commit()

    job = Job(uploaded_file_id=uploaded_file.id, plate_number=1, queue_position=1.0, status="slicing", created_at="2026-05-20T00:00:00Z", updated_at="2026-05-20T00:00:00Z")
    session.add(job)
    await session.commit()

    gcode = GcodeFile(job_id=job.id, printer_id=printer.id, path="/data/gcode/1/output.gcode")
    session.add(gcode)
    await session.commit()
    await session.refresh(gcode)
    assert gcode.id is not None
```

- [ ] **Step 2: Run tests — expect ImportError (modules don't exist yet)**

```bash
cd backend
pytest tests/test_models.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.database'`

- [ ] **Step 3: Write `backend/app/database.py`**

```python
from collections.abc import AsyncGenerator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

DATABASE_URL = "sqlite+aiosqlite:////data/themis.db"

engine = create_async_engine(DATABASE_URL, echo=False)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.execute(text("PRAGMA journal_mode=WAL"))
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session
```

- [ ] **Step 4: Write `backend/app/models.py`**

```python
from typing import Optional
from sqlalchemy import Boolean, Float, ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from .database import Base


class Printer(Base):
    __tablename__ = "printers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    printer_type: Mapped[str] = mapped_column(String(50))
    connection_config: Mapped[dict] = mapped_column(JSON)
    awaiting_plate_clear: Mapped[bool] = mapped_column(Boolean, default=False)
    orca_printer_profiles: Mapped[list] = mapped_column(JSON, default=list)
    current_orca_printer_profile: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class UploadedFile(Base):
    __tablename__ = "uploaded_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    original_filename: Mapped[str] = mapped_column(String(512))
    stored_path: Mapped[str] = mapped_column(String(1024))
    plates: Mapped[list] = mapped_column(JSON, default=list)
    uploaded_at: Mapped[str] = mapped_column(String(32))


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String(32))


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    uploaded_file_id: Mapped[int] = mapped_column(ForeignKey("uploaded_files.id"))
    plate_number: Mapped[int] = mapped_column(default=1)
    project_id: Mapped[Optional[int]] = mapped_column(ForeignKey("projects.id"), nullable=True)
    assigned_printer_id: Mapped[Optional[int]] = mapped_column(ForeignKey("printers.id"), nullable=True)
    queue_position: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    created_at: Mapped[str] = mapped_column(String(32))
    updated_at: Mapped[str] = mapped_column(String(32))


class JobPrinterConfig(Base):
    __tablename__ = "job_printer_configs"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"))
    printer_id: Mapped[int] = mapped_column(ForeignKey("printers.id"))
    print_profile: Mapped[str] = mapped_column(String(512))
    filament_profile: Mapped[str] = mapped_column(String(512))
    slice_failed: Mapped[bool] = mapped_column(Boolean, default=False)
    slice_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class GcodeFile(Base):
    __tablename__ = "gcode_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"))
    printer_id: Mapped[int] = mapped_column(ForeignKey("printers.id"))
    path: Mapped[str] = mapped_column(String(1024))
```

- [ ] **Step 5: Run model tests — expect PASS**

```bash
pytest tests/test_models.py -v
```

Expected:
```
PASSED tests/test_models.py::test_create_printer
PASSED tests/test_models.py::test_create_uploaded_file
PASSED tests/test_models.py::test_create_job_with_printer_config
PASSED tests/test_models.py::test_create_gcode_file
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/database.py backend/app/models.py backend/tests/test_models.py
git commit -m "feat: add database layer and ORM models"
```

---

## Task 3: FastAPI App & Health Endpoint

**Files:**
- Create: `backend/app/main.py`
- Create: `backend/tests/conftest.py`
- Create: `backend/tests/test_health.py`

- [ ] **Step 1: Write the failing health test**

```python
# backend/tests/test_health.py
async def test_health(client):
    response = await client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [ ] **Step 2: Write `backend/tests/conftest.py`**

```python
import pytest
import pytest_asyncio
from collections.abc import AsyncGenerator
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.main import app
from app.database import Base, get_session

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    engine = create_async_engine(TEST_DB_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_session() -> AsyncGenerator[AsyncSession, None]:
        async with factory() as s:
            yield s

    app.dependency_overrides[get_session] = override_get_session

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()
    await engine.dispose()
```

- [ ] **Step 3: Run test — expect ImportError**

```bash
pytest tests/test_health.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.main'`

- [ ] **Step 4: Write `backend/app/main.py`**

```python
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from .database import init_db


STATIC_DIR = Path(__file__).parent.parent.parent / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title="Themis", lifespan=lifespan)


@app.get("/api/v1/health")
async def health() -> dict:
    return {"status": "ok"}


if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
```

- [ ] **Step 5: Run health test — expect PASS**

```bash
pytest tests/test_health.py -v
```

Expected: `PASSED tests/test_health.py::test_health`

- [ ] **Step 6: Run full test suite to confirm no regressions**

```bash
pytest -v
```

Expected: all 5 tests PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/main.py backend/tests/conftest.py backend/tests/test_health.py
git commit -m "feat: add FastAPI app with health endpoint"
```

---

## Task 4: Frontend Scaffold

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/tsconfig.json`
- Create: `frontend/vite.config.ts`
- Create: `frontend/index.html`
- Create: `frontend/src/main.tsx`
- Create: `frontend/src/App.tsx`

- [ ] **Step 1: Initialise the frontend package**

```bash
cd frontend
npm create vite@latest . -- --template react-ts
# When prompted "Current directory is not empty" → select "Ignore files and continue"
```

This creates `package.json`, `tsconfig.json`, `vite.config.ts`, `index.html`, `src/main.tsx`, `src/App.tsx`.

- [ ] **Step 2: Replace `frontend/vite.config.ts`** to proxy API calls to the backend in dev:

```typescript
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
      '/ws': { target: 'ws://localhost:8000', ws: true },
    },
  },
})
```

- [ ] **Step 3: Replace `frontend/src/App.tsx`** with a placeholder:

```tsx
export default function App() {
  return (
    <div style={{ fontFamily: 'sans-serif', padding: '2rem' }}>
      <h1>Themis</h1>
      <p>Print farm manager — coming soon.</p>
    </div>
  )
}
```

- [ ] **Step 4: Install dependencies and verify the dev server starts**

```bash
cd frontend
npm install
npm run dev
```

Expected: Vite dev server starts on `http://localhost:5173`, browser shows "Themis — Print farm manager — coming soon."

Stop the dev server (`Ctrl+C`).

- [ ] **Step 5: Verify a production build succeeds**

```bash
npm run build
```

Expected: `frontend/dist/` directory created with `index.html` and asset files.

- [ ] **Step 6: Commit**

```bash
git add frontend/
git commit -m "feat: scaffold React + Vite frontend"
```

---

## Task 5: Docker Setup

**Files:**
- Create: `Dockerfile`
- Create: `docker-compose.yml`
- Create: `.env.example`

- [ ] **Step 1: Write `Dockerfile`**

```dockerfile
# Stage 1: build React app
FROM node:20-slim AS frontend-build
WORKDIR /build/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: Python runtime
FROM python:3.11-slim AS runtime
WORKDIR /app

# Install system deps (ffmpeg and OrcaSlicer added in a later plan)
RUN apt-get update && apt-get install -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

COPY backend/pyproject.toml ./
RUN pip install --no-cache-dir ".[dev]" || pip install --no-cache-dir .

COPY backend/app/ ./app/
COPY --from=frontend-build /build/frontend/dist/ ./frontend/dist/

RUN mkdir -p /data

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 2: Write `docker-compose.yml`**

```yaml
services:
  themis:
    build: .
    ports:
      - "8000:8000"
    volumes:
      - themis-data:/data
      - "${APPDATA}/OrcaSlicer:/root/.config/OrcaSlicer:ro"
    restart: unless-stopped

volumes:
  themis-data:
```

- [ ] **Step 3: Write `.env.example`**

```dotenv
# Copy to .env and set APPDATA to your Windows user data path.
# Docker Desktop on Windows expands this automatically.
# Example: APPDATA=C:\Users\YourName\AppData\Roaming
APPDATA=
```

- [ ] **Step 4: Build the Docker image**

```bash
docker build -t themis:dev .
```

Expected: build completes without errors. Note the image size.

- [ ] **Step 5: Run the container and verify the health endpoint**

```bash
docker run --rm -p 8000:8000 themis:dev
```

In a second terminal:
```bash
curl http://localhost:8000/api/v1/health
```

Expected: `{"status":"ok"}`

Also open `http://localhost:8000` in a browser — should show the "Themis" placeholder page served as static HTML.

Stop the container (`Ctrl+C`).

- [ ] **Step 6: Commit**

```bash
git add Dockerfile docker-compose.yml .env.example
git commit -m "feat: add multi-stage Dockerfile and docker-compose"
```

---

## Task 6: Update CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update `CLAUDE.md` with project commands**

Replace the existing `CLAUDE.md` with:

```markdown
# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

### Backend
```bash
cd backend
python -m venv .venv && .venv\Scripts\activate  # first time
pip install -e ".[dev]"

# Run dev server (auto-reload)
uvicorn app.main:app --reload --port 8000

# Run all tests
pytest -v

# Run a single test
pytest tests/test_models.py::test_create_printer -v
```

### Frontend
```bash
cd frontend
npm install          # first time
npm run dev          # dev server on :5173, proxies /api to :8000
npm run build        # production build → frontend/dist/
```

### Docker
```bash
docker build -t themis:dev .
docker compose up            # uses .env for APPDATA
docker compose up --build    # rebuild image first
```

## Architecture

Python (FastAPI) backend + React/Vite/TypeScript frontend, single Docker container. FastAPI serves the built React app as static files in production; in development, Vite's dev server proxies `/api` to the FastAPI process.

### Key design patterns

**Printer integration:** `AbstractPrinterClient` ABC with capability flags, factory + registry, `PrinterManager` singleton. See `docs/printer-interface.md` for the full pattern (ported from GroundsKeeper). Adding a vendor = add one class + one registry entry, nothing else changes.

**Queue engine:** Single asyncio background task (`queue_loop`) woken by an `asyncio.Event`. A printer is eligible for a new job only when `is_idle == True` AND `awaiting_plate_clear == False`. Slicing runs in a `ThreadPoolExecutor` to avoid blocking the event loop.

**Slicing failure recovery:** each `job_printer_configs` row has a `slice_failed` flag. On failure, the row is marked and the job requeues if any eligible printers remain; transitions to `failed` only when all configs are exhausted.

**OrcaSlicer profiles:** the `/root/.config/OrcaSlicer` directory is bind-mounted read-only from the host. `ProfileService` parses preset JSONs and filters by `compatible_printers` against the printer's `current_orca_printer_profile`.

### Database
SQLite (WAL mode) via async SQLAlchemy 2.0 + aiosqlite. Six tables: `printers`, `uploaded_files`, `projects`, `jobs`, `job_printer_configs`, `gcode_files`. No migration tool — `Base.metadata.create_all` on startup.

### Volumes (Docker)
- `/data` — SQLite file + uploaded 3MF files + sliced gcode cache
- `/root/.config/OrcaSlicer` — bind-mounted read-only from `%APPDATA%\OrcaSlicer` on Windows host

## Spec & Plans
- Design spec: `docs/superpowers/specs/2026-05-20-themis-print-farm-manager-design.md`
- Implementation plans: `docs/superpowers/plans/`
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md with commands and architecture"
```

---

## Self-Review

**Spec coverage check:**
- ✅ Single Docker container with `/data` volume and OrcaSlicer bind-mount — Task 5
- ✅ SQLite WAL mode — `database.py` `init_db()`
- ✅ All six ORM models — Task 2
- ✅ FastAPI + static file serving — `main.py`
- ✅ React + Vite scaffold with `/api` proxy — Task 4
- ✅ `queue_position` float on `Job` — models
- ✅ `orca_printer_profiles` + `current_orca_printer_profile` on `Printer` — models
- ✅ `slice_failed` + `slice_error` on `JobPrinterConfig` — models

**Placeholder scan:** No TBD, TODO, or vague steps. All code blocks are complete.

**Type consistency:** `Base`, `get_session` defined in `database.py` and imported consistently in `models.py`, `conftest.py`, and `main.py`. Model field names used in test assertions match column names defined in the model.

**Note:** The `Dockerfile` does not yet install OrcaSlicer or ffmpeg — those are added in Plan 3 and Plan 4 respectively when the services that use them are implemented.
