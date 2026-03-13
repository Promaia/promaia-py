#!/bin/sh
# One-time migration: copy legacy user data from project root into maia-data/
#
# Safe to run multiple times — only copies files that exist in the old location
# and do NOT already exist in maia-data/. Nothing is deleted; originals stay put.
#
# Usage: ./migrate-data.sh  (from the project root)
set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
MAGENTA='\033[0;95m'
BOLD='\033[1m'
NC='\033[0m'

printf "${MAGENTA}${BOLD}Promaia data migration${NC}\n"
printf "Copies legacy files from project root into maia-data/\n\n"

# Sanity check — must be run from the project root
if [ ! -d "promaia" ]; then
    printf "${RED}ERROR${NC} Run this from the Promaia project root.\n"
    exit 1
fi

if [ -d "maia-data" ]; then
    printf "maia-data/ already exists — will only copy files not already present.\n\n"
else
    printf "Creating maia-data/\n"
    mkdir -p maia-data
fi

COPIED=0
SKIPPED=0

# copy_file <src> <dest>
# Copies a single file if src exists and dest does not.
copy_file() {
    src="$1"
    dest="$2"
    if [ -f "$src" ]; then
        if [ -f "$dest" ]; then
            printf "  ${YELLOW}SKIP${NC} %s (already exists in maia-data/)\n" "$src"
            SKIPPED=$((SKIPPED + 1))
        else
            mkdir -p "$(dirname "$dest")"
            cp "$src" "$dest"
            printf "  ${GREEN}COPY${NC} %s → %s\n" "$src" "$dest"
            COPIED=$((COPIED + 1))
        fi
    fi
}

# copy_dir <src> <dest>
# Recursively copies a directory if src exists and dest does not.
copy_dir() {
    src="$1"
    dest="$2"
    if [ -d "$src" ]; then
        if [ -d "$dest" ]; then
            printf "  ${YELLOW}SKIP${NC} %s/ (already exists in maia-data/)\n" "$src"
            SKIPPED=$((SKIPPED + 1))
        else
            mkdir -p "$(dirname "$dest")"
            cp -r "$src" "$dest"
            printf "  ${GREEN}COPY${NC} %s/ → %s/\n" "$src" "$dest"
            COPIED=$((COPIED + 1))
        fi
    fi
}

# ── Core configuration files ─────────────────────────────────────────
printf "${MAGENTA}Core config files${NC}\n"
copy_file ".env"                  "maia-data/.env"
copy_file "promaia.config.json"   "maia-data/promaia.config.json"
copy_file "sync_config.json"      "maia-data/sync_config.json"
copy_file "mcp_servers.json"      "maia-data/mcp_servers.json"

# ── Content data directory ────────────────────────────────────────────
printf "\n${MAGENTA}Content data${NC}\n"
copy_dir  "data"                  "maia-data/data"

# ── Credentials ────────────────────────────────────────────────────────
printf "\n${MAGENTA}Credentials${NC}\n"
copy_dir  "credentials"             "maia-data/credentials"

# ── Debug and context logs ────────────────────────────────────────────
printf "\n${MAGENTA}Debug & context logs${NC}\n"
copy_dir  "debug_logs"            "maia-data/debug_logs"
copy_dir  "context_logs"          "maia-data/context_logs"

# ── Vector database ──────────────────────────────────────────────────
printf "\n${MAGENTA}Vector database${NC}\n"
copy_dir  "chroma_db"             "maia-data/chroma_db"

# ── Summary ──────────────────────────────────────────────────────────
printf "\n${BOLD}Done.${NC} %s copied, %s skipped.\n" "$COPIED" "$SKIPPED"

if [ "$COPIED" -gt 0 ]; then
    printf "\nOriginal files were ${BOLD}not${NC} deleted — verify everything works, then\n"
    printf "remove the old locations manually if desired.\n"
fi
