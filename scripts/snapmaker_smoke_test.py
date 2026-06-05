#!/usr/bin/env python3
"""Snapmaker U1 (Extended) Moonraker connectivity smoke test (manual, not pytest).

Reads SNAPMAKER_IP from the repo-root .env (git-ignored) and queries Moonraker:
  - GET /server/info + /printer/info  (Moonraker/Klipper up?)
  - GET /printer/objects/query for print_stats + extruders + bed (live status)
Run:  backend/.venv/Scripts/python.exe scripts/snapmaker_smoke_test.py
"""
from __future__ import annotations
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env(root: Path) -> None:
    env = root / ".env"
    if not env.exists():
        return
    for raw in env.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())


def main() -> int:
    load_env(ROOT)
    ip = os.environ.get("SNAPMAKER_IP")
    port = os.environ.get("SNAPMAKER_PORT", "7125")
    if not ip:
        print("MISSING SNAPMAKER_IP in .env")
        return 2
    import httpx
    base = f"http://{ip}:{port}"
    print(f"Snapmaker smoke test -> {base}")
    try:
        info = httpx.get(f"{base}/printer/info", timeout=8).json()["result"]
        print(f"  OK: Klipper {info.get('state')} | sw {info.get('software_version')} | host {info.get('hostname')}")
    except Exception as exc:  # noqa: BLE001
        print(f"  FAIL: /printer/info: {exc}")
        return 1
    try:
        q = (f"{base}/printer/objects/query?print_stats&display_status&heater_bed"
             f"&extruder&extruder1&extruder2&extruder3")
        st = httpx.get(q, timeout=8).json()["result"]["status"]
        ps = st.get("print_stats", {})
        print(f"  print_state={ps.get('state')} file={ps.get('filename') or '-'} "
              f"progress={st.get('display_status', {}).get('progress')}")
        print(f"  bed={st.get('heater_bed', {}).get('temperature')}  "
              f"e0={st.get('extruder', {}).get('temperature')}")
        print("  RESULT: PASS")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"  FAIL: objects/query: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
