# G2 local demo in one command: Streamlit dashboard + the n8n demo workflow on this computer.
# Nothing is sent to guests: the workflow only checks the action and replies (executed = false).
#
#   Double-click run_demo.bat, or:  powershell -ExecutionPolicy Bypass -File run_demo.ps1 [-NoN8n]
#
# First run: creates .venv (Python 3.11+), installs requirements-g2.txt, installs n8n into
# .n8n-local/ (needs Node.js 24+, download time varies), imports and publishes the workflow.
# Later runs reuse the installed n8n and check the dashboard dependencies.
param([switch]$NoN8n, [switch]$SetupOnly)  # -SetupOnly: prepare and start n8n, do not open the dashboard

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root
$N8nVersion = '2.43.8'
$N8nDir = Join-Path $Root '.n8n-local'
$N8nCmd = Join-Path $N8nDir 'node_modules\.bin\n8n.cmd'
$Port = 5690
$Webhook = "http://127.0.0.1:$Port/webhook/g2-hotel-approval-demo"
$WorkflowName = 'G2 Hotel - Approval & execution guard demo (no emails)'

function Step($msg) { Write-Host "[G2 demo] $msg" -ForegroundColor Cyan }
function Fail($msg) { Write-Host "[G2 demo] $msg" -ForegroundColor Red; Read-Host 'Press Enter to close'; exit 1 }
function N8nUp {
  try { (Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 "http://127.0.0.1:$Port/healthz/readiness").StatusCode -eq 200 } catch { $false }
}
function N8nAlive {
  try { (Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 "http://127.0.0.1:$Port/healthz").StatusCode -eq 200 } catch { $false }
}
function WaitN8n {
  $deadline = (Get-Date).AddSeconds(300)
  $nextNotice = Get-Date
  while (-not (N8nUp) -and (Get-Date) -lt $deadline) {
    if ((Get-Date) -ge $nextNotice) {
      Step 'Waiting for n8n to finish starting and activate its workflows ...'
      $nextNotice = (Get-Date).AddSeconds(20)
    }
    Start-Sleep -Seconds 2
  }
  if (-not (N8nUp)) { Fail "n8n is not ready on port $Port. Check its window; do not start a second copy." }
}

# Hold an OS file lock for the whole launch. The file can remain after a crash:
# only the open handle matters, so the next launch is not blocked by a stale file.
$LaunchLock = $null
try {
  $LaunchLock = [IO.File]::Open((Join-Path $Root '.demo-run.lock'),
    [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
} catch {
  Write-Host '[G2 demo] Another launcher is already running for this folder. Use its window; do not start a second installation.' -ForegroundColor Yellow
  exit 1
}

try {
# ---- 1. Python environment -------------------------------------------------------------
$Py = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $Py)) {
  Step 'Creating the Python environment .venv ...'
  if (Get-Command py -ErrorAction SilentlyContinue) { & py -3 -m venv .venv }
  elseif (Get-Command python -ErrorAction SilentlyContinue) { & python -m venv .venv }
  else { Fail 'Python 3.11+ not found. Install it from https://www.python.org/downloads/ (tick "Add to PATH").' }
  if (-not (Test-Path $Py)) { Fail 'Could not create .venv.' }
}
& $Py -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
if ($LASTEXITCODE -ne 0) { Fail 'Python 3.11 or newer is required. Delete .venv and install a newer Python.' }
Step 'Installing dashboard packages (requirements-g2.txt) ...'
& $Py -m pip install --disable-pip-version-check -q -r requirements-g2.txt
if ($LASTEXITCODE -ne 0) { Fail 'pip install failed (check the internet connection).' }

# ---- 2. n8n (optional) -----------------------------------------------------------------
if (-not $NoN8n) {
  $env:N8N_USER_FOLDER = Join-Path $N8nDir 'data'
  $env:N8N_PORT = "$Port"
  $env:N8N_RUNNERS_BROKER_PORT = "$($Port + 1)"
  $env:N8N_LISTEN_ADDRESS = '127.0.0.1'
  $env:N8N_DIAGNOSTICS_ENABLED = 'false'
  $env:N8N_PERSONALIZATION_ENABLED = 'false'
  if (N8nAlive) {
    WaitN8n
    Step "n8n is already running on port ${Port}: reusing it."
  } else {
    $node = Get-Command node -ErrorAction SilentlyContinue
    $major = if ($node) { [int]((& node --version) -replace '^v(\d+).*', '$1') } else { 0 }
    if ($major -lt 24) {
      Write-Host '[G2 demo] Node.js 24+ not found: starting the dashboard WITHOUT n8n.' -ForegroundColor Yellow
      Write-Host '          Install Node.js 24 LTS from https://nodejs.org and run this again to add n8n.' -ForegroundColor Yellow
      $NoN8n = $true
    } else {
      # Older launchers/manual npm commands do not hold our lock.
      $otherInstall = Get-CimInstance Win32_Process -Filter "Name = 'node.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like '*npm-cli.js*install*' -and $_.CommandLine -like "*$N8nDir*" }
      if ($otherInstall) { Fail 'npm is already installing in .n8n-local. Wait for that installation or stop it before retrying.' }
      $installReady = $false
      if (Test-Path $N8nCmd) {
        $installedVersion = (& $N8nCmd --version 2>$null | Out-String).Trim()
        $installReady = $LASTEXITCODE -eq 0 -and $installedVersion -eq $N8nVersion
      }
      if (-not $installReady) {
        Step "Installing n8n $N8nVersion. Large download; slow networks can take much longer than a few minutes. Do not launch again."
        & npm.cmd install --prefix $N8nDir "n8n@$N8nVersion" --no-audit --no-fund --loglevel=http
        if ($LASTEXITCODE -ne 0) { Fail 'n8n installation failed. See RUN_DEMO.md for ENOTEMPTY recovery, or use dashboard/run_windows.bat for Streamlit only.' }
        if (-not (Test-Path $N8nCmd)) { Fail 'npm finished but the n8n launcher is missing. See RUN_DEMO.md.' }
        $installedVersion = (& $N8nCmd --version 2>$null | Out-String).Trim()
        if ($LASTEXITCODE -ne 0 -or $installedVersion -ne $N8nVersion) { Fail 'n8n installation did not pass its version check.' }
      }
      $line = (& $N8nCmd list:workflow 2>$null) | Where-Object { $_ -like "*|$WorkflowName" } | Select-Object -First 1
      if (-not $line) {
        Step 'Importing the demo workflow (n8n/g2_approval_demo_workflow.json) ...'
        & $N8nCmd import:workflow --input="$(Join-Path $Root 'n8n\g2_approval_demo_workflow.json')" | Out-Null
        $line = (& $N8nCmd list:workflow 2>$null) | Where-Object { $_ -like "*|$WorkflowName" } | Select-Object -First 1
        if (-not $line) { Fail 'The workflow could not be imported.' }
      }
      $id = $line.Split('|')[0]
      & $N8nCmd publish:workflow --id=$id 2>$null | Out-Null
      if ($LASTEXITCODE -ne 0) { Fail 'Workflow publish failed. See n8n/README_VI.md section 6.' }
      Step 'Starting n8n in its own window (close that window to stop n8n) ...'
      Start-Process -FilePath $N8nCmd -ArgumentList 'start' -WorkingDirectory $N8nDir
      WaitN8n
      Step 'n8n is ready.'
    }
  }
}

if ($SetupOnly) { Step 'Setup finished (-SetupOnly): dashboard not started.'; exit 0 }

# ---- 3. Dashboard --------------------------------------------------------------------------
if (-not $NoN8n) { $env:G2_N8N_WEBHOOK_URL = $Webhook } else { Remove-Item Env:G2_N8N_WEBHOOK_URL -ErrorAction SilentlyContinue }
Step 'Opening the dashboard at http://localhost:8501 (Ctrl+C here to stop it) ...'
& $Py -m streamlit run dashboard/app.py --server.address 127.0.0.1 --server.port 8501 --browser.gatherUsageStats false
} finally {
  if ($LaunchLock) { $LaunchLock.Dispose() }
}
