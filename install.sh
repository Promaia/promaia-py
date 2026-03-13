#!/bin/sh
# Promaia installer — Docker-based setup
# Usage: ./install.sh  (from repo root)
set -e

# Note: Interactive prompts use simple text input (read) rather than
# arrow-key selectors. Shell scripts are fragile with cursor manipulation
# across terminals; the Python setup wizard (maia setup) handles the
# polished interactive experience via prompt_toolkit.

# ── Colors ────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
MAGENTA='\033[0;95m'
PURPLE='\033[0;35m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

# ── Banner ────────────────────────────────────────────────────────────
printf "\n"
printf "${MAGENTA}${BOLD}  🐙 Promaia Installer${NC}\n"
printf "${PURPLE}  =====================${NC}\n\n"

# ── Step 1: Check prerequisites ──────────────────────────────────────
printf "${MAGENTA}Checking prerequisites...${NC}\n"

if ! command -v docker >/dev/null 2>&1; then
    printf "  ${RED}ERROR${NC} Docker is not installed.\n"
    printf "  Install Docker Desktop: https://docs.docker.com/get-docker/\n"
    exit 1
fi
printf "  ${BLUE}OK${NC} docker found\n"

if ! docker compose version >/dev/null 2>&1; then
    printf "  ${RED}ERROR${NC} Docker Compose v2 is not available.\n"
    printf "  Docker Desktop includes Compose v2 by default.\n"
    printf "  If using Docker Engine: https://docs.docker.com/compose/install/\n"
    exit 1
fi
printf "  ${BLUE}OK${NC} docker compose v2 found\n"

DOCKER_READY=false
if command -v timeout >/dev/null 2>&1; then
    timeout 10 docker ps >/dev/null 2>&1 && DOCKER_READY=true
else
    docker ps >/dev/null 2>&1 && DOCKER_READY=true
fi
if [ "$DOCKER_READY" != "true" ]; then
    printf "  ${RED}ERROR${NC} Docker daemon is not running or not responding.\n"
    printf "  Start Docker Desktop or run: sudo systemctl start docker\n"
    exit 1
fi
printf "  ${BLUE}OK${NC} docker daemon running\n\n"

# ── Step 2: Image source detection ───────────────────────────────────
if [ -f "Dockerfile" ] && [ -d "promaia" ]; then
    printf "${YELLOW}Local source code detected.${NC}\n"
    printf "Build the image locally? (y/n) [y]: "
    read -r BUILD_LOCAL
    BUILD_LOCAL="${BUILD_LOCAL:-y}"

    if [ "$BUILD_LOCAL" = "y" ] || [ "$BUILD_LOCAL" = "Y" ]; then
        printf "\n${MAGENTA}Building image from local source...${NC}\n"
        docker compose build maia
    else
        printf "\n${MAGENTA}Pulling pre-built image...${NC}\n"
        docker pull ghcr.io/promaia/promaia:latest
    fi
else
    printf "${MAGENTA}Pulling pre-built image...${NC}\n"
    docker pull ghcr.io/promaia/promaia:latest
fi
printf "  ${GREEN}OK${NC} image ready\n\n"

# ── Step 3: Seed maia-data/ ───────────────────────────────────────────
printf "${MAGENTA}Preparing maia-data/...${NC}\n"
mkdir -p maia-data/data

if [ ! -f "maia-data/.env" ]; then
    if [ -f ".env.example" ]; then
        cp .env.example maia-data/.env
        printf "  ${GREEN}OK${NC} created maia-data/.env from .env.example\n"
    else
        printf "  ${YELLOW}Warning${NC} no .env.example found — setup will create .env\n"
    fi
else
    printf "  ${GREEN}OK${NC} maia-data/.env already exists\n"
fi

if [ ! -f "maia-data/promaia.config.json" ]; then
    if [ -f "promaia.config.template.json" ]; then
        cp promaia.config.template.json maia-data/promaia.config.json
        printf "  ${GREEN}OK${NC} created maia-data/promaia.config.json from template\n"
    fi
else
    printf "  ${GREEN}OK${NC} maia-data/promaia.config.json already exists\n"
fi

if [ ! -f "maia-data/mcp_servers.json" ]; then
    printf '{"servers":{}}' > maia-data/mcp_servers.json
    printf "  ${GREEN}OK${NC} created maia-data/mcp_servers.json (empty — configure Notion MCP here)\n"
else
    printf "  ${GREEN}OK${NC} maia-data/mcp_servers.json already exists\n"
fi

if [ ! -f "maia-data/services.json" ]; then
    cat > maia-data/services.json << 'SERVICES'
{
  "web":       { "enabled": true },
  "scheduler": { "enabled": true },
  "calendar":  { "enabled": true },
  "mail":      { "enabled": true },
  "discord":   { "enabled": false }
}
SERVICES
    printf "  ${GREEN}OK${NC} created maia-data/services.json\n"
else
    printf "  ${GREEN}OK${NC} maia-data/services.json already exists\n"
fi
printf "\n"

# ── Step 4: Install CLI wrapper ──────────────────────────────────────
MAIA_DIR="$(pwd)"
MAIA_INSTALLED=""

sed "s|__MAIA_DIR__|${MAIA_DIR}|g" maia.sh > /tmp/maia-wrapper.sh
chmod +x /tmp/maia-wrapper.sh

printf "${MAGENTA}Install 'maia' command so you can run it from anywhere?${NC}\n"
printf "  [1] /usr/local/bin/maia  ${BOLD}(all users, needs sudo)${NC}\n"
printf "  [2] ~/.local/bin/maia    (current user only)\n"
printf "  [3] Skip\n"
printf "Choice [1]: "
read -r INSTALL_CHOICE
INSTALL_CHOICE="${INSTALL_CHOICE:-1}"

case "$INSTALL_CHOICE" in
    1)
        sudo cp /tmp/maia-wrapper.sh /usr/local/bin/maia
        printf "  ${GREEN}OK${NC} installed to /usr/local/bin/maia\n"
        MAIA_INSTALLED="yes"
        ;;
    2)
        mkdir -p "$HOME/.local/bin"
        cp /tmp/maia-wrapper.sh "$HOME/.local/bin/maia"
        printf "  ${GREEN}OK${NC} installed to ~/.local/bin/maia\n"
        MAIA_INSTALLED="yes"
        case ":$PATH:" in
            *":$HOME/.local/bin:"*) ;;
            *) printf "  ${YELLOW}Note:${NC} add ~/.local/bin to your PATH if it isn't already\n" ;;
        esac
        ;;
    3|*)
        printf "  Skipped.\n"
        ;;
esac
rm -f /tmp/maia-wrapper.sh

# ── Step 5: Run setup wizard (final step) ────────────────────────────
printf "\n${MAGENTA}Starting setup wizard...${NC}\n\n"

if [ "$MAIA_INSTALLED" = "yes" ]; then
    docker compose run --rm -e PROMAIA_FROM_INSTALLER=1 -e PROMAIA_MAIA_INSTALLED=1 maia setup
else
    docker compose run --rm -e PROMAIA_FROM_INSTALLER=1 maia setup
fi
