# Claude Agent SDK Subprocess Blocker

**Date:** 2025-12-22 Morning
**Status:** Blocked on Agent SDK subprocess communication

## Problem

We've successfully implemented the foundation for agent orchestration:
- ✅ Intent classifier (knowledge vs action)
- ✅ Context serializer (Promaia data → markdown)
- ✅ Agent session manager structure

However, we're **blocked** on actually getting responses from Claude Code agents via the Agent SDK.

## What Works

```python
# Agent spawns successfully
session = AgentSession(task="...", context={...})
await session.spawn()  # ✅ Works - subprocess starts

# Initial query sends
await session.client.query("Tell me what you can see")  # ✅ Works

# But receive_messages() hangs forever
async for message in session.client.receive_messages():
    print(message)  # ❌ Never reaches here
```

**Logs show:**
```
INFO: Agent spawned successfully and task sent
INFO: Using bundled Claude Code CLI: .../claude_agent_sdk/_bundled/claude
```

Then... nothing. The subprocess is running but not producing output.

## Root Cause Analysis

### Theory 1: Permission/Approval Flow
The Claude Code CLI might be waiting for user approval for tool use or other permissions. The SDK subprocess can't show interactive prompts, so it hangs.

**Evidence:**
- SDK docs mention "permission modes" (default, acceptEdits, bypassPermissions)
- We're using default mode, which prompts for dangerous tools
- Subprocess can't show prompts → hangs

### Theory 2: Initialization Incomplete
The subprocess might need additional initialization steps we're missing.

**Evidence:**
- SDK has methods like `set_permission_mode()`, `set_model()`
- These might need to be called before `query()`
- Documentation is sparse on proper flow

### Theory 3: Message Parsing Issues
Maybe messages ARE coming but our parsing logic doesn't handle them correctly.

**Counter-evidence:**
- We're using the SDK's own `receive_messages()` generator
- Should handle parsing internally
- But we never see ANY output, not even errors

## What We've Tried

1. **Different query formats** - Simple strings, different tasks
2. **Import fixes** - Relative vs absolute imports (worked)
3. **Sending initial query** - Added `await client.query(task)` after connect
4. **Timeout handling** - Background processes with 60s timeouts

All attempts result in the same behavior: spawn succeeds, then hangs on `receive_messages()`.

## The Fundamental Issue

The Claude Agent SDK is designed for **interactive CLI applications**, not programmatic embedding. It expects:
- User to approve tool uses
- Terminal for displaying output
- Interactive back-and-forth

But we want:
- Programmatic, non-interactive execution
- Capture all output
- No user prompts during execution

**This is an architecture mismatch.**

## Alternative Approaches

### Option 1: Direct Anthropic SDK + Manual MCP Calls ⭐ (Recommended)

Instead of using Agent SDK's subprocess model, use the Anthropic SDK directly and manually call MCPs when needed.

**Architecture:**
```python
from anthropic import Anthropic

client = Anthropic()

# 1. User asks: "Send Federico an email"
# 2. Promaia detects ACTION intent
# 3. Call Claude with tools defined

response = client.messages.create(
    model="claude-sonnet-4-5-20250929",
    system=create_system_prompt(context),
    messages=[{"role": "user", "content": query}],
    tools=[
        {
            "name": "gmail_send_email",
            "description": "Send an email via Gmail",
            "input_schema": {...}
        },
        # ... other tools
    ]
)

# 4. If Claude requests tool use
if response.stop_reason == "tool_use":
    for block in response.content:
        if block.type == "tool_use":
            # Call MCP manually
            result = call_gmail_mcp(block.name, block.input)

            # Continue conversation with result
            response = client.messages.create(
                model="...",
                messages=[...previous_messages, tool_result]
            )
```

**Pros:**
- Direct control over conversation flow
- No subprocess overhead
- Can implement tool calling ourselves
- Lightweight and fast

**Cons:**
- Need to implement MCP calling manually
- More code to write and maintain
- No built-in "agent loop"

### Option 2: Build Lightweight MCP Client

Create a simple MCP client that can call MCP servers directly, without the full Agent SDK.

**Architecture:**
```python
class SimpleMCPClient:
    def __init__(self, mcp_config):
        # Load MCP servers from .claude.json
        self.servers = {}
        for name, config in mcp_config.items():
            self.servers[name] = subprocess.Popen(
                [config['command'], *config['args']],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                env=config['env']
            )

    async def call_tool(self, server_name, tool_name, params):
        # Send JSON-RPC request to MCP server
        request = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": params}
        }
        # ... communicate with subprocess ...
```

**Pros:**
- Direct MCP access
- Lighter than full Agent SDK
- More control

**Cons:**
- Need to implement MCP protocol
- JSON-RPC communication
- Still dealing with subprocesses

### Option 3: Keep Agent SDK but Fix Permissions

Try to configure Agent SDK to not require approvals.

**Approach:**
```python
options = ClaudeAgentOptions(
    model="...",
    system_prompt="...",
    mcp_servers={...},
    # Try different permission modes
    permission_mode="bypassPermissions"  # If this exists
)
```

**Pros:**
- Keep current architecture
- Might "just work"

**Cons:**
- Not sure if `permission_mode` can be set in options
- Might still have other blocking issues
- Still heavyweight subprocess model

## Recommendation

**Go with Option 1: Direct Anthropic SDK + Manual MCP Calls**

### Reasoning

1. **We already have most pieces:**
   - Intent classifier ✅
   - Context serializer ✅
   - System prompt generation ✅

2. **Just need to add:**
   - Tool definitions for MCPs
   - MCP subprocess calling
   - Tool result handling

3. **Matches our architecture:**
   - Promaia stays in control
   - Direct API calls (fast)
   - Can implement "group chat" UX ourselves

4. **Proven pattern:**
   - This is how Claude API with tools works
   - Well-documented
   - Many examples available

## Next Steps

1. **Pivot to Anthropic SDK approach**
2. **Define Gmail MCP tool schema**
3. **Implement MCP subprocess caller**
4. **Test with simple "send email" action**
5. **Iterate from there**

## Code to Write

### 1. Tool Definitions

```python
# promaia/agent/mcp_tools.py

GMAIL_TOOLS = [
    {
        "name": "gmail_send_message",
        "description": "Send an email via Gmail",
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "array", "items": {"type": "string"}},
                "subject": {"type": "string"},
                "body": {"type": "string"}
            },
            "required": ["to", "subject", "body"]
        }
    },
    # ... other Gmail tools
]

NOTION_TOOLS = [
    {
        "name": "notion_create_page",
        "description": "Create a new page in Notion",
        "input_schema": {...}
    }
]
```

### 2. MCP Caller

```python
# promaia/agent/mcp_caller.py

import subprocess
import json

class MCPCaller:
    def __init__(self, mcp_config):
        """Initialize with MCP server configs from .claude.json"""
        self.servers = {}
        # Start MCP server subprocesses

    async def call_tool(self, server_name: str, tool_name: str, params: dict):
        """Call an MCP tool and return result"""
        # Send JSON-RPC to MCP server
        # Get response
        # Return result
```

### 3. Agent Manager (Simplified)

```python
# promaia/agent/anthropic_agent.py

from anthropic import Anthropic

class AnthropicAgent:
    def __init__(self, context, task):
        self.client = Anthropic()
        self.context = context
        self.task = task
        self.mcp_caller = MCPCaller(load_mcp_config())

    async def execute(self):
        """Execute the task using Anthropic API + MCPs"""
        system_prompt = create_system_prompt(self.context, self.task)
        tools = GMAIL_TOOLS + NOTION_TOOLS

        response = self.client.messages.create(
            model="claude-sonnet-4-5-20250929",
            system=system_prompt,
            messages=[{"role": "user", "content": self.task}],
            tools=tools
        )

        # Handle tool use
        while response.stop_reason == "tool_use":
            # Process tool calls
            # Call MCPs
            # Continue conversation
            pass
```

This approach is **more work upfront** but gives us **full control** and **actually works**.

## Decision Point

Should we:
1. **Pivot now** to Anthropic SDK approach?
2. **Keep debugging** Agent SDK subprocess issues?
3. **Try Option 3** (permission modes) first?

My recommendation: **Pivot to Option 1** (Anthropic SDK). The Agent SDK subprocess model is fundamentally misaligned with our use case.
