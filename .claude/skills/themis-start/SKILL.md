---
name: themis-start
description: Start the Themis dev servers (backend :8001, frontend :5173) bound to all interfaces — required for Tailscale access via http://dionysus:5173
---

# Themis: Start Dev Servers

Announce: "Starting Themis dev servers…"

Run:

```powershell
& '.claude\skills\themis-start\scripts\start.ps1'
```

The script kills stale processes on :8001, opens backend and frontend in new terminal windows, polls until both are ready, then prints the access URLs. Relay the output to the user.

## Troubleshooting (only consult if the script reports failure)

| Symptom | Fix |
|---|---|
| `:8001` still occupied after kill | `Get-Process python3.13 \| Stop-Process -Force` then re-run |
| `curl localhost:8001` returns wrong HTML | IPv6 fallback hitting stale process — kill remaining PID shown in netstat |
| Fleet shows empty on first load | Normal — printers reconnect asynchronously within a few seconds |
| `dionysus` not resolving | Tailscale must be running on the remote device |
| Changes not hot-reloading | venv built with Store Python — rebuild with python.org Python (`py -0` to list) |
