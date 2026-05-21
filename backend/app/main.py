import os
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from .database import init_db


_default_static = Path(__file__).parent.parent.parent / "frontend" / "dist"
STATIC_DIR = Path(os.environ.get("THEMIS_STATIC_DIR", str(_default_static)))


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
