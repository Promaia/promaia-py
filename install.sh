#!/bin/sh
# Promaia installer — standalone Docker-based setup
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/Promaia/promaia-py/main/install.sh | sh
#   sh install.sh
#   sh install.sh --location /opt/maia
set -e

# ── Parse arguments ───────────────────────────────────────────────────
INSTALL_DIR=""
while [ $# -gt 0 ]; do
    case "$1" in
        --location)
            INSTALL_DIR="$2"
            shift 2
            ;;
        *)
            printf "Unknown option: %s\n" "$1" >&2
            exit 1
            ;;
    esac
done

# Note: Interactive prompts use simple text input (read) rather than
# arrow-key selectors. Shell scripts are fragile with cursor manipulation
# across terminals; the Python setup wizard (maia setup) handles the
# polished interactive experience via prompt_toolkit.
#
# All `read` calls use </dev/tty so prompts work when piped via curl | sh.

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
printf "${MAGENTA}${BOLD}  Promaia Installer${NC}\n"
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

# ── Step 2: Determine install directory & detect dev repo ────────────
IS_DEV=false
if [ -f "Dockerfile" ] && [ -d "promaia" ]; then
    IS_DEV=true
fi

if [ -z "$INSTALL_DIR" ]; then
    if [ "$IS_DEV" = "true" ]; then
        INSTALL_DIR="$(pwd)"
    else
        INSTALL_DIR="$HOME/.promaia-py/app"
    fi
fi

# Resolve to absolute path
case "$INSTALL_DIR" in
    /*) ;; # already absolute
    *)  INSTALL_DIR="$(cd "$(dirname "$INSTALL_DIR")" 2>/dev/null && pwd)/$(basename "$INSTALL_DIR")" ;;
esac

mkdir -p "$INSTALL_DIR"
printf "${MAGENTA}Install directory:${NC} %s\n" "$INSTALL_DIR"
if [ "$IS_DEV" = "true" ]; then
    printf "  ${YELLOW}Dev repo detected${NC} — using local source\n"
fi
printf "\n"

# ── Step 3: Pull image ───────────────────────────────────────────────
printf "${MAGENTA}Pulling pre-built image...${NC}\n"
docker pull ghcr.io/promaia/promaia-py:latest
printf "  ${GREEN}OK${NC} image ready\n\n"

# ── Step 4: Scaffold / seed files ────────────────────────────────────
if [ "$IS_DEV" = "true" ]; then
    # ── Dev repo: seed maia-data/ inline, offer pilots mount ─────────
    printf "${MAGENTA}Preparing maia-data/ (dev mode)...${NC}\n"
    mkdir -p "$INSTALL_DIR/maia-data/data"

    if [ "$(id -u)" = "0" ]; then
        chown -R 1000:1000 "$INSTALL_DIR/maia-data"
    else
        chown -R 1000:1000 "$INSTALL_DIR/maia-data" 2>/dev/null || chmod -R a+rwX "$INSTALL_DIR/maia-data"
    fi

    if [ ! -f "$INSTALL_DIR/maia-data/.env" ]; then
        if [ -f "$INSTALL_DIR/.env.example" ]; then
            cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/maia-data/.env"
            printf "  ${GREEN}OK${NC} created maia-data/.env from .env.example\n"
        else
            printf "  ${YELLOW}Warning${NC} no .env.example found — setup will create .env\n"
        fi
    else
        printf "  ${GREEN}OK${NC} maia-data/.env already exists\n"
    fi

    if [ ! -f "$INSTALL_DIR/maia-data/promaia.config.json" ]; then
        if [ -f "$INSTALL_DIR/promaia.config.template.json" ]; then
            cp "$INSTALL_DIR/promaia.config.template.json" "$INSTALL_DIR/maia-data/promaia.config.json"
            printf "  ${GREEN}OK${NC} created maia-data/promaia.config.json from template\n"
        fi
    else
        printf "  ${GREEN}OK${NC} maia-data/promaia.config.json already exists\n"
    fi

    if [ ! -f "$INSTALL_DIR/maia-data/mcp_servers.json" ]; then
        printf '{"servers":{}}' > "$INSTALL_DIR/maia-data/mcp_servers.json"
        printf "  ${GREEN}OK${NC} created maia-data/mcp_servers.json\n"
    else
        printf "  ${GREEN}OK${NC} maia-data/mcp_servers.json already exists\n"
    fi

    if [ ! -f "$INSTALL_DIR/maia-data/services.json" ]; then
        cat > "$INSTALL_DIR/maia-data/services.json" << 'SERVICES'
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

    # Offer pilots mount
    printf "${YELLOW}Local source code detected.${NC}\n"
    printf "Mount local repo into the container (for development)? (y/n) [y]: "
    read -r USE_LOCAL </dev/tty
    USE_LOCAL="${USE_LOCAL:-y}"

    if [ "$USE_LOCAL" = "y" ] || [ "$USE_LOCAL" = "Y" ]; then
        ENV_FILE="$INSTALL_DIR/.env"
        if [ -f "$ENV_FILE" ] && grep -q '^COMPOSE_FILE=' "$ENV_FILE"; then
            sed -i 's|^COMPOSE_FILE=.*|COMPOSE_FILE=docker-compose.pilots.yaml|' "$ENV_FILE"
        else
            echo 'COMPOSE_FILE=docker-compose.pilots.yaml' >> "$ENV_FILE"
        fi
        printf "  ${GREEN}OK${NC} set COMPOSE_FILE=docker-compose.pilots.yaml in .env\n"
        printf "  Local source will be bind-mounted into containers.\n"
    fi
    printf "\n"
else
    # ── End-user: scaffold via docker run ────────────────────────────
    printf "${MAGENTA}Scaffolding install files...${NC}\n"
    docker run --rm --user root --entrypoint sh \
        -v "$INSTALL_DIR:/output" \
        ghcr.io/promaia/promaia-py:latest \
        /app/scaffold.sh /output
    printf "  ${GREEN}OK${NC} files extracted\n\n"
fi

# ── Step 5: Install CLI wrapper ──────────────────────────────────────
MAIA_INSTALLED=""

sed "s|__MAIA_DIR__|${INSTALL_DIR}|g" "$INSTALL_DIR/maia.sh" > /tmp/maia-wrapper.sh
chmod +x /tmp/maia-wrapper.sh

printf "${MAGENTA}Install 'maia' command so you can run it from anywhere?${NC}\n"
printf "  [1] /usr/local/bin/maia  ${BOLD}(all users, needs sudo)${NC}\n"
printf "  [2] ~/.local/bin/maia    (current user only)\n"
printf "  [3] Skip\n"
printf "Choice [1]: "
read -r INSTALL_CHOICE </dev/tty
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

# ── Step 6: Run setup wizard ─────────────────────────────────────────
printf "\n${MAGENTA}Starting setup wizard...${NC}\n\n"

cd "$INSTALL_DIR"
if [ "$MAIA_INSTALLED" = "yes" ]; then
    docker compose run --rm -e PROMAIA_FROM_INSTALLER=1 -e PROMAIA_MAIA_INSTALLED=1 maia setup
else
    docker compose run --rm -e PROMAIA_FROM_INSTALLER=1 maia setup
fi
