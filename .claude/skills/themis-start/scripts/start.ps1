# Start Themis dev servers: uvicorn :8001 + vite :5173
# Both bind to 0.0.0.0 for Tailscale/LAN access via http://dionysus:5173

$Root = (Resolve-Path "$PSScriptRoot\..\..\..\..").Path

# --- Step 1: Clear port 8001 ---
Write-Host "Clearing stale processes on :8001..." -ForegroundColor Cyan
Get-Process python3.13 -ErrorAction SilentlyContinue | Stop-Process -Force -Confirm:$false
(netstat -ano | Select-String '(:8001).*LISTENING') -replace '.*LISTENING\s+', '' |
    Sort-Object -Unique |
    Where-Object { $_ -match '^\d+$' } |
    ForEach-Object { Stop-Process -Id ([int]$_) -Force -Confirm:$false -ErrorAction SilentlyContinue }
Start-Sleep -Milliseconds 400

$still = netstat -ano | Select-String ':8001.*LISTENING'
if ($still) {
    Write-Host "  WARNING: :8001 still occupied after kill attempt" -ForegroundColor Yellow
    $still | ForEach-Object { Write-Host "  $_" }
}

# --- Step 2: Start backend ---
Write-Host "Starting backend (uvicorn :8001)..." -ForegroundColor Cyan
$backendCmd = "Set-Location '$Root\backend'; .venv\Scripts\Activate.ps1; uvicorn app.main:app --reload --port 8001 --host 0.0.0.0"
Start-Process powershell -ArgumentList "-NoExit", "-Command", $backendCmd

Start-Sleep -Seconds 2

if (netstat -ano | Select-String ':8001.*LISTENING') {
    Write-Host "  backend: listening on :8001" -ForegroundColor Green
} else {
    Write-Host "  backend: not yet listening — may still be starting" -ForegroundColor Yellow
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
    Write-Host "  frontend: did not respond within 20 s — check the Vite window" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Themis is up:" -ForegroundColor Green
Write-Host "  Local:     http://localhost:5173"
Write-Host "  Tailscale: http://dionysus:5173"
Write-Host "  LAN:       http://192.168.0.227:5173"
