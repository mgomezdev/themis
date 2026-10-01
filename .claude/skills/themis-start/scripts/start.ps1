# Start Themis dev servers: uvicorn :8001 + vite :5173
# Both bind to 0.0.0.0 for Tailscale/LAN access via http://dionysus:5173

$Root = (Resolve-Path "$PSScriptRoot\..\..\..\..").Path

# --- Step 1: Clear port 8001 ---
Write-Host "Clearing stale processes on :8001..." -ForegroundColor Cyan
(netstat -ano | Select-String '(:8001).*LISTENING') -replace '.*LISTENING\s+', '' |
    Sort-Object -Unique |
    Where-Object { $_ -match '^\d+$' } |
    ForEach-Object { Stop-Process -Id ([int]$_) -Force -Confirm:$false -ErrorAction SilentlyContinue }
Start-Sleep -Milliseconds 400

$still = netstat -ano | Select-String ':8001.*LISTENING'
if ($still) {
    Write-Host "  ERROR: :8001 still occupied after kill attempt - not starting a second backend" -ForegroundColor Red
    $still | ForEach-Object { Write-Host "  $_" }
    exit 1
}

# --- Step 2: Start backend ---
Write-Host "Starting backend (uvicorn :8001)..." -ForegroundColor Cyan
if (-not (docker ps --filter name=themis-orca-1 --filter name=concordia-orca-1 -q 2>$null)) {
    Write-Host "  NOTE: Orca container not detected. Start it with:" -ForegroundColor Yellow
    Write-Host "    docker compose -f docker-compose.yml -f docker-compose.dev.yml up orca" -ForegroundColor Yellow
}
$backendCmd = "Set-Location '$Root\backend'; .venv\Scripts\Activate.ps1; `$env:LAMINUS_SIDECAR_URL='http://localhost:5000'; uvicorn app.main:app --reload --port 8001 --host 0.0.0.0"
Start-Process powershell -ArgumentList "-NoExit", "-Command", $backendCmd

# Poll the health route (max 30 s) - proves the new backend is serving, not just that something holds the port.
$backendReady = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 1
    try {
        $null = Invoke-WebRequest -Uri "http://localhost:8001/api/v1/health" -TimeoutSec 2 -UseBasicParsing -ErrorAction Stop
        $backendReady = $true
        break
    } catch {}
}

if ($backendReady) {
    Write-Host "  backend: ready on :8001" -ForegroundColor Green
} else {
    Write-Host "  ERROR: backend did not answer on :8001 within 30 s - check the uvicorn window" -ForegroundColor Red
    exit 1
}

# --- Step 3: Start frontend ---
Write-Host "Starting frontend on :5173..." -ForegroundColor Cyan
$frontendCmd = "Set-Location '$Root\frontend'; npm run dev"
Start-Process powershell -ArgumentList "-NoExit", "-Command", $frontendCmd

# --- Step 4: Poll until frontend is ready (max 20 s) ---
Write-Host "Waiting for frontend..." -ForegroundColor Cyan
$ready = $false
for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Seconds 1
    try {
        $null = Invoke-WebRequest -Uri "http://localhost:5173" -TimeoutSec 2 -UseBasicParsing -ErrorAction Stop
        $ready = $true
        break
    } catch {}
}

Write-Host ""
if ($ready) {
    Write-Host "  frontend: ready at :5173" -ForegroundColor Green
} else {
    Write-Host "  frontend: did not respond within 20 s - check the Vite window" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Themis is up:" -ForegroundColor Green
Write-Host "  Local:     http://localhost:5173"
Write-Host "  Tailscale: http://dionysus:5173"
Write-Host "  LAN:       http://192.168.0.227:5173"
