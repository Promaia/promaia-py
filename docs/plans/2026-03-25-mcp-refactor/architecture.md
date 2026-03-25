# Agent Architecture and MCP Integration Map

**Date:** 2026-03-25

## Three agent engines

### 1. `agentic_turn()` — Direct Anthropic loop

- **File:** `promaia/agents/agentic_turn.py:3648`
- **API:** Direct `anthropic.Anthropic`, model `claude-sonnet-4-6`
- **Tool loop:** Custom iteration (max 40 rounds), tools as Anthropic native dicts
- **MCP:** **None.** `agent.mcp_tools` is read as feature flags only — decides which hardcoded `*_TOOL_DEFINITIONS` to include. All tool execution is Python code in `ToolExecutor` (same file, ~line 1280).
- **Used by:** `maia chat`, mail responder, tag-to-chat (Slack/Discord mentions), `run_goal.py`

### 2. `AgentExecutor` — Scheduled agent executor (dual-path)

- **File:** `promaia/agents/executor.py:115`
- **SDK path** (`sdk_enabled=True`): `claude_agent_sdk.ClaudeSDKClient` spawns a subprocess. `mcp_loader.load_mcp_servers_for_agent()` provides `{command, args, env}` dicts. **SDK manages its own stdio MCP connections** — our `McpProtocolClient` is never called.
- **Legacy path** (fallback): `PromaiLLMAdapter` (Anthropic/OpenAI/Gemini). No MCP.
- **Used by:** scheduler background loop, `maia agent run <name>`

### 3. `Orchestrator` — Multi-step goal decomposition

- **File:** `promaia/agents/orchestrator.py`
- **Planner:** Direct Anthropic API (Haiku) to decompose goals into tasks
- **Execution:** Delegates each task to `agentic_turn()`. Inherits its no-MCP-protocol situation.
- **Used by:** `run_goal.py --orchestrate`

## Supporting components

| Component | File | Role |
|---|---|---|
| `ToolExecutor` | `agentic_turn.py:1280` | Central tool router for engine 1. Hardcoded Python implementations for query, gmail, calendar, notion, sheets, web, messaging. |
| `MailToolExecutor` | `mail/agentic_responder.py` | Extends `ToolExecutor`. Intercepts mail tools (draft queue). |
| `ConversationManager` | `agents/conversation_manager.py:495` | Direct Anthropic for in-conversation responses. Used by Orchestrator. |
| `PromaiLLMAdapter` | `nlq/nl_orchestrator.py:47` | Multi-client adapter (Anthropic/OpenAI/Gemini). Used by executor legacy path. |
| `_generate_plan()` | `agentic_turn.py:3552` | Lightweight Haiku call to decompose complex requests into steps. |

## MCP integration points

### A. Our MCP client (new code from this refactor)

Used by three callers that connect to MCP servers directly:

1. **Chat interface** (`chat/interface.py`) — 7 callsites. Creates `McpClient`, connects to tool servers, discovers tools for system prompt injection. This is the primary path.
2. **Notion prompts** (`notion/prompts.py`) — Creates `McpClient`, connects to Notion MCP server, calls a tool, disconnects.
3. **Web API** (`web/routers/mcp.py`) — `/mcp/servers` and `/mcp/tools` endpoints. Note: `/mcp/execute` and `/mcp/search` call `McpClient.call_tool()` which **does not exist** (pre-existing bug, not introduced by this refactor).

### B. MCP loader for SDK (unchanged)

- `agents/mcp_loader.py` reads `mcp_servers.json`, filters by `agent.mcp_tools`, outputs `{command, args, env}` for the SDK
- Only used by `AgentExecutor` SDK path
- Now skips `streamable_http` servers (SDK can't use them)

### C. Feature flags in agentic_turn (no protocol involved)

- `agent.mcp_tools` list gates which tool definition sets get included
- e.g., `"gmail" in mcp_tools` -> include `GMAIL_TOOL_DEFINITIONS`
- Tools execute as Python, not via MCP protocol

## Entry point → engine → MCP summary

| Entry point | Engine | MCP protocol? |
|---|---|---|
| `maia chat` | `agentic_turn()` | Feature flags only |
| `maia agent run` | `AgentExecutor` (SDK or legacy) | SDK: yes (SDK-managed stdio). Legacy: no |
| `maia agent run --orchestrate` | `Orchestrator` -> `agentic_turn()` | Feature flags only |
| Scheduler (cron) | `AgentExecutor` | Same as `maia agent run` |
| Mail responder | `agentic_turn()` + `MailToolExecutor` | Feature flags only |
| Tag-to-chat (mentions) | `agentic_turn()` | Feature flags only |
| Web API `/mcp/*` | `McpClient` direct | **Yes — our new code** |
| Notion prompts | `McpClient` direct | **Yes — our new code** |
| Chat MCP setup | `McpClient` direct | **Yes — our new code** |
