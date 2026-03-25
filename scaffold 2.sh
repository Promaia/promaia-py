#!/bin/sh
# scaffold.sh — runs INSIDE the Docker image to extract install files
# Invoked by install.sh via: docker run --rm --user root -v "$DIR:/output" ... /app/scaffold.sh /output
set -e

OUTPUT="${1:?Usage: scaffold.sh /output}"

# ── Always overwrite (should match image version) ─────────────────────
cp /app/docker-compose.yml "$OUTPUT/docker-compose.yml"
cp /app/maia.sh            "$OUTPUT/maia.sh"
cp /app/maia.bat           "$OUTPUT/maia.bat"

# ── Only seed if not already present ──────────────────────────────────
mkdir -p "$OUTPUT/maia-data/data"

if [ ! -f "$OUTPUT/maia-data/.env" ]; then
    cp /app/.env.example "$OUTPUT/maia-data/.env"
fi

if [ ! -f "$OUTPUT/maia-data/promaia.config.json" ]; then
    cp /app/promaia.config.template.json "$OUTPUT/maia-data/promaia.config.json"
fi

if [ ! -f "$OUTPUT/maia-data/mcp_servers.json" ]; then
    printf '{"servers":{}}' > "$OUTPUT/maia-data/mcp_servers.json"
fi

if [ ! -f "$OUTPUT/maia-data/services.json" ]; then
    cat > "$OUTPUT/maia-data/services.json" << 'EOF'
{
  "web":       { "enabled": true },
  "scheduler": { "enabled": true },
  "calendar":  { "enabled": true },
  "mail":      { "enabled": true },
  "discord":   { "enabled": false }
}
EOF
fi

# ── Fix ownership for the container's maia user (uid 1000) ───────────
chown -R 1000:1000 "$OUTPUT/maia-data"
