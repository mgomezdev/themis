#!/usr/bin/env python3
"""Bambu LAN connectivity smoke test (manual diagnostic — NOT part of pytest).

Reads BAMBU_IP / BAMBU_ACCESS_CODE / BAMBU_SERIAL_NUMBER from the repo-root .env
(or the environment) and exercises the real BambuMQTTClient against the printer:
  1. MQTT connect on 8883 + wait for a live status report
  2. FTPS login on 990 (the sliced-file upload path) — NO file is uploaded
The access code is never printed. Secrets come only from .env (git-ignored).

Run:  backend/.venv/Scripts/python.exe scripts/bambu_smoke_test.py
"""
from __future__ import annotations
import os
import ssl
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))


def load_env(root: Path) -> None:
    env = root / ".env"
    if not env.exists():
        return
    for raw in env.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip())


def mask(s: str) -> str:
    return (s[:2] + "*" * max(0, len(s) - 2)) if s else "(empty)"


def main() -> int:
    load_env(ROOT)
    ip = os.environ.get("BAMBU_IP")
    serial = os.environ.get("BAMBU_SERIAL_NUMBER")
    access = os.environ.get("BAMBU_ACCESS_CODE")
    if not (ip and serial and access):
        print("MISSING creds — set BAMBU_IP / BAMBU_SERIAL_NUMBER / BAMBU_ACCESS_CODE in .env")
        return 2

    print(f"Bambu smoke test -> ip={ip} serial={serial} access_code={mask(access)}")

    from app.services.bambu_mqtt import BambuMQTTClient, _ImplicitFTP_TLS, FTPS_PORT

    client = BambuMQTTClient(ip_address=ip, serial_number=serial, access_code=access)

    # ---- 1) MQTT (8883) ----
    print("\n[1/2] MQTT connect (8883)...")
    client.connect()
    deadline = time.time() + 15
    while time.time() < deadline and not client.connected:
        time.sleep(0.5)
    mqtt_connected = client.connected
    if not mqtt_connected:
        print("  FAIL: MQTT did not connect within 15s (check LAN mode + access code).")
        client.disconnect()
        return 1
    print("  OK: MQTT connected. Requesting full status report...")
    client.request_status_update()
    deadline = time.time() + 10
    while time.time() < deadline and client.state.state == "unknown":
        time.sleep(0.5)

    s = client.state
    status_received = s.state != "unknown"
    print(f"  state={s.state}  stg_cur={s.stg_cur}  model={s.model}  firmware={s.firmware}")
    print(f"  temps={s.temperatures}")
    print(f"  ams_trays={len(s.ams_trays)}  ams_tray_now={s.ams_tray_now}")
    try:
        fil = client.get_loaded_filaments()
        print(f"  loaded filaments ({len(fil)}): {fil}")
    except Exception as exc:  # noqa: BLE001 - diagnostic
        print(f"  loaded filaments: error {exc}")

    # ---- 2) FTPS (990) — auth only, no upload ----
    print("\n[2/2] FTPS login (990, upload path) -- no file uploaded...")
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ftp_ok = False
    try:
        ftp = _ImplicitFTP_TLS(context=ctx)
        ftp.connect(ip, FTPS_PORT, timeout=20)
        ftp.login("bblp", access)
        ftp.prot_p()
        pwd = ftp.pwd()
        names = ftp.nlst()
        print(f"  OK: FTPS auth succeeded. pwd={pwd}  entries={len(names)}  sample={names[:5]}")
        ftp.quit()
        ftp_ok = True
    except Exception as exc:  # noqa: BLE001 - diagnostic
        print(f"  FAIL: FTPS error: {exc}")

    client.disconnect()

    print("\n=== SUMMARY ===")
    print(f"  MQTT connected:   {mqtt_connected}")
    print(f"  status received:  {status_received}")
    print(f"  FTPS upload path: {ftp_ok}")
    passed = mqtt_connected and ftp_ok
    print("  RESULT:", "PASS" if passed else "NEEDS ATTENTION")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
