# Claude Agent SDK Integration Findings

**Date:** 2025-12-22
**Status:** Architecture Mismatch Discovered

## Summary

After attempting to integrate the Claude Agent SDK into Promaia, we've discovered a fundamental architectural mismatch. The Claude Agent SDK is **not** a wrapper around the Anthropic API - it's a programmatic interface to the **Claude Code CLI tool itself**.

## What is the Claude Agent SDK Actually?

The Claude Agent SDK (`claude-agent-sdk`) is designed to:
- Programmatically control the Claude Code CLI
- Build applications that interact with Claude Code as a subprocess
- Use Claude Code's capabilities (including MCPs) from Python/Node.js
- Essentially: **Claude Code as a Service**

## Architecture Discovery

From reading the SDK source code (`venv/lib/python3.12/site-packages/claude_agent_sdk/client.py`):

```python
# Line 93: Uses subprocess to run Claude Code CLI
from ._internal.transport.subprocess_cli import SubprocessCLITransport

# Lines 87-89: connect() starts the CLI subprocess
async def connect(self, prompt: str | AsyncIterable[dict[str, Any]] | None = None):
    # Creates subprocess running Claude Code
```

The SDK:
1. Spawns Claude Code CLI as a subprocess
2. Communicates via stdio (standard input/output)
3. Parses Claude Code's output messages
4. Sends user messages to the CLI

## Why This Is a Problem for Promaia

### Our Original Goal
- Replace Promaia's Anthropic API calls with "agentic" Claude
- Add MCP capabilities (Gmail, Notion) to Promaia
- Maintain direct context control (`/e`, browse mode)

### The Reality
The Agent SDK introduces a **heavyweight subprocess architecture**:

```
Promaia
  ↓
Agent SDK (Python client)
  ↓
Claude Code CLI (subprocess)
  ↓
Anthropic API
  ↓
Claude Sonnet 4.5
```

This means:
- **Spawning a full Claude Code instance** for every Promaia chat session
- Running Claude Code (which is itself a complex CLI) as a library
- Double-layered architecture: Promaia wrapping Claude Code wrapping Claude API
- Potential issues: process management, resource usage, permission handling

## Test Results

### What Worked
✅ Installation: `pip install claude-agent-sdk` (v0.1.18)
✅ MCP Configuration: Loading from `.claude.json`
✅ Client Creation: `ClaudeSDKClient(options=...)`
✅ Connection: `await client.connect()` (spawns subprocess)

### What Failed / Blocked
❌ The test hangs after connection - likely waiting for subprocess initialization
❌ Unclear message flow between SDK and CLI subprocess
❌ Heavyweight architecture for what should be a lightweight API wrapper

## Alternative Approaches

### Option 1: Direct Anthropic SDK + Manual MCP Calling

**Pros:**
- Lightweight: Direct API calls to Anthropic
- Full control over conversation flow
- No subprocess overhead
- Can still use MCPs by calling them manually

**Cons:**
- Need to manually implement MCP tool calling
- More code to maintain
- No "agentic loop" out of the box

**Implementation:**
```python
from anthropic import Anthropic
import subprocess
import json

# Call Anthropic API directly
client = Anthropic()
response = client.messages.create(...)

# When Claude requests a tool, call MCP manually
if tool_use := extract_tool_use(response):
    mcp_result = call_mcp_tool(tool_use)
    # Continue conversation with tool result
```

### Option 2: Build Promaia as an MCP Server

**Pros:**
- Promaia tools accessible to any MCP client (Claude Code, Claude.ai, etc.)
- Clean separation: Promaia is a tool provider, not an AI wrapper
- Users can use Promaia from multiple interfaces

**Cons:**
- Doesn't solve the "context first, then agent" workflow
- Users would still need Claude Code or similar client
- Loses direct control over conversation flow

**Implementation:**
```python
# Build Promaia MCP server
from mcp import MCPServer

server = MCPServer()

@server.tool()
async def query_sql(query: str, workspace: str):
    # Existing Promaia query logic
    ...

# Users run: claude mcp add promaia
# Then use from Claude Code: "search my journal for yesterday"
```

### Option 3: Hybrid - Use Anthropic SDK with Standard MCP Client

**Pros:**
- Lightweight Anthropic API for core chat
- Still get MCP capabilities via standard MCP client library
- Can use existing MCPs (Gmail, Notion)

**Cons:**
- Need to integrate MCP client library (likely complex)
- May still have subprocess overhead for MCPs
- Two different tool systems (Promaia native + MCP)

## Recommendation

Based on this analysis, I recommend **Option 1: Direct Anthropic SDK + Manual MCP Calling**.

### Why?

1. **Maintains Promaia's Value Proposition**: "Fast context loading BEFORE talking to Claude"
2. **Lightweight**: No subprocess overhead
3. **Full Control**: We control exactly when context is loaded vs when AI is invoked
4. **Incremental**: Start with core Anthropic API, add MCP calling later
5. **Simpler**: Fewer moving parts than Agent SDK approach

### Implementation Plan

1. Keep current Promaia chat interface using Anthropic API
2. Add MCP tool calling infrastructure:
   - Spawn MCP servers on demand (Gmail, Notion)
   - Parse tool_use blocks from Claude's responses
   - Call appropriate MCP tool via stdio
   - Feed results back to Claude
3. Maintain all existing Promaia features:
   - `/e` command for direct source loading
   - Browse mode for context editing
   - Query tools (query_sql, query_vector, query_source)
4. Add new MCP-based tools gradually

## Next Steps

1. Document this finding with the user
2. Get approval for recommended approach (Option 1)
3. If approved, create implementation plan for Anthropic SDK + MCP integration
4. If not approved, discuss alternative options

## Files Created During Investigation

- `promaia/agent/__init__.py` - Agent SDK imports
- `promaia/agent/sdk_adapter_simple.py` - Simplified SDK adapter
- `promaia/agent/sdk_adapter.py` - Full SDK adapter (abandoned)
- `test_agent_simple.py` - SDK test script
- This document

## Lessons Learned

- Always read the source code when documentation is unclear
- "Agent SDK" doesn't mean "AI API wrapper" - it means "CLI automation tool"
- Architecture matters: subprocesses have real overhead
- Sometimes the simplest approach (direct API calls) is best
