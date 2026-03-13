#!/bin/sh
# Maia CLI — runs commands inside a persistent Docker container
# Install: ./install.sh will offer to place this on your PATH
set -e

MAIA_DIR="__MAIA_DIR__"
cd "$MAIA_DIR"

# Check that Docker is reachable
if ! docker info >/dev/null 2>&1; then
    echo "Error: Docker is not running. Please start Docker Desktop and try again." >&2
    exit 1
fi

# restart-container: recreate all service containers
if [ "$1" = "restart-container" ]; then
    docker compose up -d --force-recreate
    echo "Containers restarted."
    exit 0
fi

# Ensure all service containers are running (no-op if already up)
docker compose up -d 2>/dev/null

exec docker compose exec maia maia "$@"
