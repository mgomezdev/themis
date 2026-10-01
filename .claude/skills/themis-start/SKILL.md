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

The script kills only the process(es) listening on :8001 (never other Python processes), opens backend and frontend in new terminal windows, polls until both answer, then prints the access URLs. It exits non-zero if :8001 can't be freed or the backend never answers. Relay the output to the user.

## Troubleshooting (only consult if the script reports failure)

| Symptom | Fix |
|---|---|
| `:8001` still occupied after kill | Find the PID in the `netstat -ano` line the script printed, check what it is (`Get-Process -Id <pid>`), `Stop-Process -Id <pid> -Force`, then re-run |
| `curl localhost:8001` returns wrong HTML | IPv6 fallback hitting stale process — kill remaining PID shown in netstat |
| Fleet shows empty on first load | Normal — printers reconnect asynchronously within a few seconds |
| `dionysus` not resolving | Tailscale must be running on the remote device |
| Changes not hot-reloading | venv built with Store Python — rebuild with python.org Python (`py -0` to list) |
