# Discord Bot + MCP Architecture
**Date:** 2025-12-20

## Overview

This document describes the architecture for integrating Promaia with Discord using the Claude Agent SDK and Model Context Protocol (MCP) servers.

## The Vision

Create a Discord bot that:
- Uses Claude's agentic loop (multi-step reasoning, tool calling)
- Has access to Promaia's aggregated context (journal, stories, CMS, etc.)
- Can perform write operations via service-specific MCPs (Gmail, Notion)
- Runs 24/7 on Render

## Key Architectural Decisions

### 1. Use Claude Agent SDK (Not Just Claude API)

**Why:** We want Claude Code's agentic capabilities (tool calling, multi-step reasoning, decision making) but accessible from Discord.

**Solution:** Use the Claude Agent SDK, which provides:
- Full agentic loop
- Tool calling (Bash, Read, Write, etc.)
- Multi-step reasoning
- Python/TypeScript API

**Install:**
```bash
pip install claude-agent-sdk
```

### 2. Promaia as Context Aggregation Layer (MCP Server)

**Promaia's Role:**
- Sync data locally (Notion → SQLite, CMS → SQLite, etc.)
- Expose synced data via MCP resources
- Provide query tools for database access
- **Does NOT handle writes** - delegates to service-specific MCPs

**MCP Resources to Expose:**
```
@promaia:journal[:N]          # Last N days of journal
@promaia:stories              # Stories database
@promaia:cms                  # CMS content
@promaia:workspace_context    # Full workspace context
@promaia:draft:ID             # Specific draft
@promaia:thread:ID            # Email thread
```

**MCP Tools to Expose:**
```python
promaia_query_source(source: str)      # Query by source
promaia_query_vector(query: str)       # Vector search
promaia_sync_database(database: str)   # Trigger sync
promaia_get_pending_tasks()            # Task queue
```

### 3. Multi-MCP Architecture

**Discord Bot connects to multiple MCP servers:**

1. **Promaia MCP** - Context/data aggregation (read-only)
2. **Gmail MCP** - Email operations (read + write)
   - Send emails with attachments
   - Create/manage drafts
   - Reply to threads
   - Search emails

3. **Notion MCP** (Official) - Notion operations (read + write)
   - Create pages
   - Update pages
   - Append blocks
   - Manage properties
   - Create databases

4. **Future MCPs** - Add as needed for other integrations

### 4. System Prompt vs. Conversation History

**Key Learning:** MCP resources don't inject into system prompt - they attach to conversation history.

**How it works:**
```
User: @promaia:journal
[MCP server returns journal data]
Conversation now contains attachment with journal data
Claude sees attachment for all subsequent messages
```

**Result:** Functionally equivalent to system prompt injection - context persists throughout conversation.

## Architecture Diagram

```
┌─────────────────┐
│  Discord User   │
└────────┬────────┘
         │
         v
┌─────────────────────────────────────┐
│     Discord Bot (on Render)         │
│  - Handles Discord events           │
│  - Manages conversation context     │
└────────┬────────────────────────────┘
         │
         v
┌─────────────────────────────────────┐
│    Claude Agent SDK                 │
│  - Agentic loop                     │
│  - Tool calling                     │
│  - Multi-step reasoning             │
└────────┬────────────────────────────┘
         │
         v
┌─────────────────────────────────────┐
│         MCP Servers                 │
│  ┌─────────────────────────────┐   │
│  │  Promaia MCP                │   │
│  │  - Context aggregation      │   │
│  │  - Database queries         │   │
│  │  - Vector search            │   │
│  └─────────────────────────────┘   │
│                                     │
│  ┌─────────────────────────────┐   │
│  │  Gmail MCP                  │   │
│  │  - Send/receive emails      │   │
│  │  - Draft management         │   │
│  └─────────────────────────────┘   │
│                                     │
│  ┌─────────────────────────────┐   │
│  │  Notion MCP (Official)      │   │
│  │  - Create/update pages      │   │
│  │  - Manage databases         │   │
│  └─────────────────────────────┘   │
└─────────────────────────────────────┘
         │
         v
┌─────────────────────────────────────┐
│  Data Sources (Local + Synced)      │
│  - Notion databases                 │
│  - Gmail threads                    │
│  - CMS content                      │
│  - Journal entries                  │
│  - Stories/tasks                    │
└─────────────────────────────────────┘
```

## Data Flow

### Local Machine (Your Computer)
- **Promaia CLI** - `maia chat`, `maia sync` - primary interface
- **Data Storage** - All databases (.database files), vector embeddings
- **Connectors** - Notion, CMS, Gmail sync happens locally
- **Credentials** - API keys, workspace configs stay local

### Server (Render)
- **Discord Bot Process** - Runs 24/7, connected to Discord
- **Claude Agent SDK** - Agentic capabilities
- **Promaia MCP Server** - Exposes synced database copies
- **Read-Only Data** - Synced copies of databases
- **Limited Credentials** - Only Discord token + Anthropic/Google API keys

### Data Sync Strategy

**Option 1: Periodic Sync (Recommended)**
```bash
# Local machine
maia sync          # Updates local databases
./sync_to_s3.sh    # Uploads to Cloudflare R2

# Render server
# Polls R2 every 10 minutes
# Downloads if changed
# Reloads MCP context
```

**Option 2: Real-time Sync**
```bash
# Local machine runs sync
maia sync          # Updates local DBs
# Triggers webhook to Render
# Render reloads immediately
```

## Implementation Plan

### Phase 1: Promaia MCP Server
1. Create MCP server at `promaia/mcp/server.py`
2. Expose resources for common queries
3. Expose tools for database operations
4. Test locally with Claude Code
5. Document available resources/tools

### Phase 2: Discord Bot with Agent SDK
1. Install Claude Agent SDK
2. Create Discord bot using discord.py
3. Integrate Agent SDK for message handling
4. Connect to Promaia MCP server
5. Test locally

### Phase 3: Multi-MCP Integration
1. Add Gmail MCP connection
2. Add Notion MCP connection
3. Configure MCP routing/permissions
4. Test cross-MCP workflows

### Phase 4: Deployment
1. Create Dockerfile
2. Set up Render service (Background Worker)
3. Configure environment variables
4. Set up data sync (local → R2 → Render)
5. Deploy and test
6. Monitor and iterate

## Deployment Configuration (Render)

### Service Type
**Background Worker** (recommended)
- Runs continuously
- No HTTP endpoint needed
- Perfect for Discord bot's WebSocket connection
- More cost-effective than Web Service

### Environment Variables
```bash
DISCORD_BOT_TOKEN=xxx
ANTHROPIC_API_KEY=xxx
GOOGLE_API_KEY=xxx
WORKSPACE=koii
PROMAIA_MCP_HOST=localhost:3000
GMAIL_MCP_HOST=localhost:3001
NOTION_MCP_HOST=localhost:3002
```

### Data Storage
- **Render Persistent Disk** for database files
- **Cloudflare R2** for sync storage
- **Local SQLite** for conversation state

## What Promaia Does vs. Doesn't Do

### Promaia DOES:
✅ Sync data locally (Notion, CMS, Gmail, etc.)
✅ Store data in SQLite databases
✅ Expose data via MCP resources
✅ Provide query tools (vector search, filters)
✅ Aggregate context from multiple sources

### Promaia DOESN'T:
❌ Send emails (Gmail MCP does this)
❌ Create Notion pages (Notion MCP does this)
❌ Implement its own agentic loop (Agent SDK does this)
❌ Handle Discord events (Discord bot does this)

**Result:** Promaia becomes a focused, maintainable context aggregation layer.

## Benefits of This Architecture

1. **Separation of Concerns**
   - Promaia = context/data
   - MCPs = service operations
   - Agent SDK = agentic reasoning
   - Discord bot = interface

2. **No Duplication**
   - Use official MCPs where available
   - Don't rebuild what exists
   - Focus on Promaia's unique value

3. **Maintainability**
   - Smaller, focused codebase
   - Leverage official tools
   - Easier to debug

4. **Flexibility**
   - Add new MCPs easily
   - Switch interfaces (Discord → Slack → Web)
   - Keep data local while bot is cloud-hosted

5. **Security**
   - Minimal credentials on server
   - Read-only data access
   - Service-specific auth handled by MCPs

## Future Enhancements

- **Additional MCPs:** Slack, Linear, GitHub, etc.
- **Custom Tools:** Promaia-specific operations
- **Webhooks:** Real-time sync triggers
- **Analytics:** Usage tracking, performance monitoring
- **Multi-workspace:** Support multiple users/workspaces

## References

- [Claude Agent SDK Documentation](https://platform.claude.com/docs/en/api/agent-sdk/overview)
- [Model Context Protocol Specification](https://modelcontextprotocol.io)
- [Official Notion MCP](https://github.com/makenotion/notion-mcp-server)
- [Gmail MCP Servers](https://mcpservers.org)
- [Claude Code MCP Integration](https://code.claude.com/docs/en/mcp.md)
