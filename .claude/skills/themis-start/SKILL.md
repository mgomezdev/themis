---
name: themis-start
description: Start the Themis dev servers (backend :8001, frontend :5173) bound to all interfaces — required for Tailscale access via http://dionysus:5173
---

# Themis: Start Dev Servers

Start both servers with the correct bindings. After this skill runs the app is reachable at:

| Interface | URL |
|---|---|
| Local | http://localhost:5173 |
| Tailscale | http://dionysus:5173 |
| LAN | http://192.168.0.227:5173 |

**Announce at start:** "Starting Themis dev servers…"

---

## Step 1 — Clear stale processes on :8001

`python3.13` processes (stale `SimpleHTTPServer` artifacts) can squat on port 8001 and intercept
backend requests. Kill them before starting.

```powershell
Get-Process python3.13 -ErrorAction SilentlyContinue | Stop-Process -Force -Confirm:$false
# Belt-and-suspenders: also kill anything still holding :8001
(netstat -ano | Select-String '(:8001).*LISTENING') -replace '.*LISTENING\s+','' | Sort-Object -Unique |
    ForEach-Object { Stop-Process -Id $_ -Force -Confirm:$false -ErrorAction SilentlyContinue }
Start-Sleep -Milliseconds 400
```

Confirm port is free:
```powershell
netstat -ano | Select-String ':8001'
# Expected: only 127.0.0.1:8001 LISTENING (after backend starts), or nothing yet
```

---

## Step 2 — Start backend

**Critical flags:** `--host 0.0.0.0` is required. Without it uvicorn binds to 127.0.0.1 only
and direct Tailscale access to the backend port fails.

Run in background from the project root:

```powershell
cd backend
.venv\Scripts\Activate.ps1
uvicorn app.main:app --reload --port 8001 --host 0.0.0.0
```

Use `run_in_background: true`. Wait ~2 s, then verify:

```powershell
netstat -ano | Select-String ':8001.*LISTENING'
# Expected: 0.0.0.0:8001 and [::]:8001 entries
```

> **venv gotcha:** build the venv with the **python.org** interpreter, not Microsoft Store Python.
> `py -0` lists installed interpreters. Store Python hides `C:\Program Files` (OrcaSlicer not found)
> and breaks `--reload`. See CLAUDE.md for the full explanation.

---

## Step 3 — Start frontend

`vite.config.ts` already sets `host: true` and `allowedHosts: ['dionysus']` — no extra flags needed.
Vite's proxy forwards `/api → http://localhost:8001` and `/ws → ws://localhost:8001` server-side,
so all browser API calls work regardless of how the user accesses the app.

Run in background from the project root:

```powershell
cd frontend
npm run dev
```

Use `run_in_background: true`. Vite is ready when the log shows `Local: http://localhost:5173/`.

---

## Step 4 — Verify both servers

```powershell
# Backend: should return JSON fleet array
curl -s -4 http://localhost:8001/api/v1/fleet

# Frontend: should return HTML
curl -s -4 http://localhost:5173 | Select-String '<title>'
```

Report the access URLs to the user.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `:8001` still shows `python3.13` after kill | `Get-Process python3.13 \| Stop-Process -Force` then re-check |
| `curl localhost:8001` returns wrong HTML | IPv6 fallback hitting stale process — use `curl -4` to confirm; kill remaining pids |
| Fleet shows empty on first load | Normal — printers reconnect asynchronously within a few seconds |
| `dionysus` not resolving | Tailscale must be running on the remote device |
| Changes not reloading | venv built with Store Python — rebuild with python.org Python |
