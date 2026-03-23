# Promaia installer — standalone Docker-based setup
# Usage:
#   iwr -useb https://raw.githubusercontent.com/Promaia/promaia-py/main/install.ps1 | iex
#   .\install.ps1
#   .\install.ps1 -Location C:\maia
param(
    [string]$Location = ""
)
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# Note: Interactive prompts use simple text input (Read-Host) rather than
# arrow-key selectors. Shell scripts are fragile with cursor manipulation
# across terminals; the Python setup wizard (maia setup) handles the
# polished interactive experience via prompt_toolkit.

# ── Banner ────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "  Promaia Installer" -ForegroundColor Magenta
Write-Host "  =====================" -ForegroundColor DarkMagenta
Write-Host ""

# ── Step 1: Check prerequisites ──────────────────────────────────────
Write-Host "Checking prerequisites..." -ForegroundColor Magenta

try {
    $null = Get-Command docker -ErrorAction Stop
    Write-Host "  OK docker found" -ForegroundColor Blue
} catch {
    Write-Host "  ERROR: Docker is not installed." -ForegroundColor Red
    Write-Host "  Install Docker Desktop: https://docs.docker.com/get-docker/"
    exit 1
}

try {
    $null = docker compose version 2>&1
    if ($LASTEXITCODE -ne 0) { throw "compose not found" }
    Write-Host "  OK docker compose v2 found" -ForegroundColor Blue
} catch {
    Write-Host "  ERROR: Docker Compose v2 is not available." -ForegroundColor Red
    Write-Host "  Docker Desktop includes Compose v2 by default."
    exit 1
}

try {
    $proc = Start-Process docker -ArgumentList "ps" -WindowStyle Hidden -PassThru
    if (-not $proc.WaitForExit(10000)) {
        try { $proc.Kill() } catch {}
        throw "Docker daemon is not responding (timed out)"
    }
    if ($proc.ExitCode -ne 0) { throw "daemon not running" }
    Write-Host "  OK docker daemon running" -ForegroundColor Blue
} catch {
    Write-Host "  ERROR: Docker daemon is not running." -ForegroundColor Red
    Write-Host "  Start Docker Desktop."
    exit 1
}

Write-Host ""

# ── Step 2: Determine install directory & detect dev repo ────────────
$isDev = (Test-Path "Dockerfile") -and (Test-Path "promaia" -PathType Container)

if (-not $Location) {
    if ($isDev) {
        $Location = (Get-Location).Path
    } else {
        $Location = Join-Path $env:USERPROFILE ".promaia-py\app"
    }
}

# Resolve to absolute path
$Location = [System.IO.Path]::GetFullPath($Location)
if (-not (Test-Path $Location)) {
    New-Item -ItemType Directory -Path $Location -Force | Out-Null
}

Write-Host "Install directory: $Location" -ForegroundColor Magenta
if ($isDev) {
    Write-Host "  Dev repo detected - using local source" -ForegroundColor Yellow
}
Write-Host ""

# ── Step 3: Pull image ───────────────────────────────────────────────
Write-Host "Pulling pre-built image..." -ForegroundColor Magenta
docker pull ghcr.io/promaia/promaia-py:latest
if ($LASTEXITCODE -ne 0) {
    Write-Host "  Pull failed." -ForegroundColor Red
    exit 1
}
Write-Host "  OK image ready" -ForegroundColor Green
Write-Host ""

# ── Step 4: Scaffold / seed files ────────────────────────────────────
if ($isDev) {
    # ── Dev repo: seed maia-data/ inline, offer pilots mount ─────────
    Write-Host "Preparing maia-data/ (dev mode)..." -ForegroundColor Magenta
    if (-not (Test-Path "$Location\maia-data\data" -PathType Container)) {
        New-Item -ItemType Directory -Path "$Location\maia-data\data" -Force | Out-Null
    }

    if (-not (Test-Path "$Location\maia-data\.env")) {
        if (Test-Path "$Location\.env.example") {
            Copy-Item "$Location\.env.example" "$Location\maia-data\.env"
            Write-Host "  OK created maia-data/.env from .env.example" -ForegroundColor Green
        } else {
            Write-Host "  Warning: no .env.example found - setup will create .env" -ForegroundColor Yellow
        }
    } else {
        Write-Host "  OK maia-data/.env already exists" -ForegroundColor Green
    }

    if (-not (Test-Path "$Location\maia-data\promaia.config.json")) {
        if (Test-Path "$Location\promaia.config.template.json") {
            Copy-Item "$Location\promaia.config.template.json" "$Location\maia-data\promaia.config.json"
            Write-Host "  OK created maia-data/promaia.config.json from template" -ForegroundColor Green
        }
    } else {
        Write-Host "  OK maia-data/promaia.config.json already exists" -ForegroundColor Green
    }

    if (-not (Test-Path "$Location\maia-data\mcp_servers.json")) {
        '{"servers":{}}' | Set-Content "$Location\maia-data\mcp_servers.json" -Encoding UTF8
        Write-Host "  OK created maia-data/mcp_servers.json" -ForegroundColor Green
    } else {
        Write-Host "  OK maia-data/mcp_servers.json already exists" -ForegroundColor Green
    }

    if (-not (Test-Path "$Location\maia-data\services.json")) {
        @'
{
  "web":       { "enabled": true },
  "scheduler": { "enabled": true },
  "calendar":  { "enabled": true },
  "mail":      { "enabled": true },
  "discord":   { "enabled": false }
}
'@ | Set-Content "$Location\maia-data\services.json" -Encoding UTF8
        Write-Host "  OK created maia-data/services.json" -ForegroundColor Green
    } else {
        Write-Host "  OK maia-data/services.json already exists" -ForegroundColor Green
    }
    Write-Host ""

    # Offer pilots mount
    Write-Host "Local source code detected." -ForegroundColor Yellow
    $useLocal = Read-Host "Mount local repo into the container (for development)? (y/n) [y]"
    if (-not $useLocal) { $useLocal = "y" }

    if ($useLocal -eq "y") {
        $envFile = Join-Path $Location ".env"
        if ((Test-Path $envFile) -and (Select-String -Path $envFile -Pattern '^COMPOSE_FILE=' -Quiet)) {
            (Get-Content $envFile) -replace '^COMPOSE_FILE=.*', 'COMPOSE_FILE=docker-compose.pilots.yaml' |
                Set-Content $envFile -Encoding UTF8
        } else {
            Add-Content $envFile 'COMPOSE_FILE=docker-compose.pilots.yaml'
        }
        Write-Host "  OK set COMPOSE_FILE=docker-compose.pilots.yaml in .env" -ForegroundColor Green
        Write-Host "  Local source will be bind-mounted into containers."
    }
    Write-Host ""
} else {
    # ── End-user: scaffold via docker run ────────────────────────────
    Write-Host "Scaffolding install files..." -ForegroundColor Magenta

    # Convert Windows path to Docker-compatible format
    $dockerPath = $Location -replace '\\', '/' -replace '^([A-Za-z]):', '/$1'
    $dockerPath = $dockerPath.Substring(0, 2).ToLower() + $dockerPath.Substring(2)

    docker run --rm --user root --entrypoint sh `
        -v "${dockerPath}:/output" `
        ghcr.io/promaia/promaia-py:latest `
        /app/scaffold.sh /output

    if ($LASTEXITCODE -ne 0) {
        Write-Host "  Scaffold failed." -ForegroundColor Red
        exit 1
    }
    Write-Host "  OK files extracted" -ForegroundColor Green
    Write-Host ""
}

# ── Step 5: Install CLI wrapper ──────────────────────────────────────
$maiaInstalled = $false

$wrapperContent = (Get-Content "$Location\maia.bat" -Raw) -replace '__MAIA_DIR__', $Location

Write-Host "Install 'maia' command so you can run it from anywhere?" -ForegroundColor Magenta
Write-Host "  [1] Install to $env:LOCALAPPDATA\Maia (recommended)"
Write-Host "  [2] Skip"
$choice = Read-Host "Choice [1]"
if (-not $choice) { $choice = "1" }

if ($choice -eq "1") {
    $wrapperDir = "$env:LOCALAPPDATA\Maia"
    New-Item -ItemType Directory -Path $wrapperDir -Force | Out-Null
    $wrapperContent | Set-Content "$wrapperDir\maia.bat" -Encoding ASCII
    Write-Host "  OK installed to $wrapperDir\maia.bat" -ForegroundColor Green

    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($userPath -notlike "*$wrapperDir*") {
        [Environment]::SetEnvironmentVariable("Path", "$userPath;$wrapperDir", "User")
        Write-Host "  OK added $wrapperDir to user PATH" -ForegroundColor Green
        Write-Host "  Restart your terminal for PATH changes to take effect" -ForegroundColor Yellow
    } else {
        Write-Host "  OK $wrapperDir already on PATH" -ForegroundColor Green
    }
    $maiaInstalled = $true
} else {
    Write-Host "  Skipped."
}

# ── Step 6: Run setup wizard ─────────────────────────────────────────
Write-Host ""
Write-Host "Starting setup wizard..." -ForegroundColor Magenta
Write-Host ""

Push-Location $Location
try {
    if ($maiaInstalled) {
        docker compose run --rm -e PROMAIA_FROM_INSTALLER=1 -e PROMAIA_MAIA_INSTALLED=1 maia setup
    } else {
        docker compose run --rm -e PROMAIA_FROM_INSTALLER=1 maia setup
    }
} finally {
    Pop-Location
}
