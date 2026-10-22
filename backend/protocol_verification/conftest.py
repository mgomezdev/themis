"""Shared plumbing for the real-protocol verification suite (see README.md). Everything skips unless the
relevant THEMIS_VERIFY_* environment variables point at a real printer."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))      # make `app` importable when run from anywhere


def pytest_collection_modifyitems(items):
    for item in items:
        item.add_marker(pytest.mark.real_protocol)


def _env(name: str) -> str | None:
    return os.environ.get(name) or None


@pytest.fixture(scope="session")
def allow_write() -> bool:
    return os.environ.get("THEMIS_VERIFY_ALLOW_WRITE") == "1"


@pytest.fixture
def require_write(allow_write):
    if not allow_write:
        pytest.skip("set THEMIS_VERIFY_ALLOW_WRITE=1 to run tests that write to the printer")


@pytest.fixture
def sacrificial_name() -> str:
    return f"themis-verify-{int(time.time())}.gcode"


SACRIFICIAL_BODY = b"; themis protocol verification - safe to delete\nG28\n"


@pytest.fixture
def net():
    from app.services.discovery_net import RealNetwork
    return RealNetwork()


@pytest.fixture(scope="session")
def discovery_cfg():
    rng = _env("THEMIS_VERIFY_DISCOVERY_RANGE")
    if not rng:
        pytest.skip("set THEMIS_VERIFY_DISCOVERY_RANGE (e.g. 192.168.7.0/24) plus the per-vendor THEMIS_VERIFY_*_HOST/URL "
                    "of printers inside it")
    return {"range": rng}


@pytest.fixture(scope="session")
def bambu_cfg():
    host, code = _env("THEMIS_VERIFY_BAMBU_HOST"), _env("THEMIS_VERIFY_BAMBU_ACCESS_CODE")
    if not (host and code):
        pytest.skip("set THEMIS_VERIFY_BAMBU_HOST and THEMIS_VERIFY_BAMBU_ACCESS_CODE")
    return {"host": host, "access_code": code}


@pytest.fixture(scope="session")
def moonraker_cfg():
    url = _env("THEMIS_VERIFY_MOONRAKER_URL")
    if not url:
        pytest.skip("set THEMIS_VERIFY_MOONRAKER_URL (e.g. http://192.168.7.30:7125)")
    return {"url": url.rstrip("/"), "api_key": _env("THEMIS_VERIFY_MOONRAKER_API_KEY")}


@pytest.fixture(scope="session")
def elegoo_cfg():
    host = _env("THEMIS_VERIFY_ELEGOO_HOST")
    if not host:
        pytest.skip("set THEMIS_VERIFY_ELEGOO_HOST")
    return {"host": host, "port": int(_env("THEMIS_VERIFY_ELEGOO_PORT") or 3030)}


@pytest.fixture(scope="session")
def spoolman():
    """A Spoolman instance plus the id of a TEST spool: `.http` (httpx.Client with base_url) and `.spool_id`.
    The write checks overwrite that spool's weight (and restore it) — never point this at a spool you care about."""
    import httpx
    from types import SimpleNamespace
    url, spool_id = _env("THEMIS_VERIFY_SPOOLMAN_URL"), _env("THEMIS_VERIFY_SPOOLMAN_SPOOL_ID")
    if not (url and spool_id):
        pytest.skip("set THEMIS_VERIFY_SPOOLMAN_URL (e.g. http://spoolman:7912) and THEMIS_VERIFY_SPOOLMAN_SPOOL_ID "
                    "(a spool with a filament weight set; the write checks modify and then restore it)")
    key = _env("THEMIS_VERIFY_SPOOLMAN_API_KEY")
    with httpx.Client(base_url=url.rstrip("/"), headers={"X-API-Key": key} if key else {}, timeout=15) as http:
        yield SimpleNamespace(http=http, spool_id=int(spool_id))
