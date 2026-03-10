FROM python:3.13-slim AS base

LABEL org.opencontainers.image.source=https://github.com/Promaia/promaia-py
LABEL org.opencontainers.image.description="Promaia personal AI assistant"

WORKDIR /app

# System deps for opencv, chromadb native extensions, build tools,
# and Node.js for MCP servers (Notion, etc.) that run via npx
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libgl1 \
    libglib2.0-0 \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

# Pre-install Notion MCP servers so npx doesn't cold-start on every run
RUN npm install -g \
    @notionhq/notion-mcp-server \
    && npm cache clean --force

# Install CPU-only PyTorch first (sentence-transformers pulls torch;
# the CPU wheel is ~200MB vs ~2GB for the CUDA version)
RUN pip install --no-cache-dir \
    torch --extra-index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN pip install --no-cache-dir -e .

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
