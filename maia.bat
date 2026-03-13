@echo off
rem Maia CLI — runs commands inside a persistent Docker container
rem Install: install.ps1 will offer to place this on your PATH
setlocal
set "MAIA_DIR=__MAIA_DIR__"
pushd "%MAIA_DIR%"

rem Check that Docker is reachable
docker info >nul 2>&1
if errorlevel 1 (
    echo Error: Docker is not running. Please start Docker Desktop and try again.
    popd
    exit /b 1
)

rem restart-container: recreate all service containers
if "%~1"=="restart-container" (
    docker compose up -d --force-recreate
    echo Containers restarted.
    popd
    exit /b 0
)

rem Ensure all service containers are running (no-op if already up)
docker compose up -d >nul 2>&1

docker compose exec maia maia %*
popd
