FROM python:3.13-slim AS base

WORKDIR /app

# System deps for chromadb native extensions, build tools,
# and Node.js for MCP servers (Notion, etc.) that run via npx
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

# Pre-install Notion MCP servers so npx doesn't cold-start on every run
RUN npm install -g \
    @notionhq/notion-mcp-server \
    && npm cache clean --force

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN pip install --no-cache-dir -e . \
    && chmod +x /app/scaffold.sh

ENV PROMAIA_DOCKER=1

# Non-root user — required because the Claude Agent SDK (bundled CLI)
# refuses --dangerously-skip-permissions when running as root.
RUN useradd -m -s /bin/bash maia \
    && chown -R maia:maia /app \
    && mkdir -p /home/maia/.claude /home/maia/.promaia/logs \
    && chown -R maia:maia /home/maia/.claude /home/maia/.promaia

USER maia

# Data directory lives in a volume
VOLUME /app/data

ENTRYPOINT ["maia"]
CMD ["--help"]
