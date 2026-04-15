# MCP Refactor Status

**Date:** 2026-03-25
**Branch:** `mcp-rewrite-wip`
**Phase:** Client-side complete. Ready for user-facing MCP server configuration.

## Goal

Enable users to connect Promaia to their own custom MCP servers hosted externally (Streamable HTTP). This is NOT about migrating Promaia's built-in tool servers (query, gmail, calendar) to HTTP — those work fine on stdio and are out of scope.

## What was done

Replaced the hand-rolled JSON-RPC stdio client in `promaia/mcp/protocol.py` with the official `mcp` library's `ClientSession`. The client now supports both stdio and Streamable HTTP transports, so users can point Promaia at remote MCP servers they host elsewhere.

### Files changed

| File | What changed |
|---|---|
| `promaia/mcp/protocol.py` | **Rewritten.** ~270 lines of custom JSON-RPC replaced with `mcp.client.ClientSession` wrapper. Supports `stdio_client` and `streamablehttp_client`. Uses `AsyncExitStack` to hold transport context open. Same public API: `connect()`, `list_tools()`, `call_tool()`, `disconnect()`, `is_connected()`, `get_server_info()`, `get_capabilities()`. |
| `promaia/mcp/result_adapter.py` | **New file.** Two functions: `adapt_call_tool_result(CallToolResult) -> dict` and `adapt_tool_list(ListToolsResult) -> list[dict]`. Converts official `mcp` types to plain dicts that `execution.py` expects. |
| `promaia/mcp/client.py` | Updated `connect_to_server()` to branch on `config.transport`. Passes `url`/`headers` for HTTP, `command`/`args`/`env`/`working_dir` for stdio. |
| `promaia/config/mcp_servers.py` | Added `transport` field (`"stdio"` default, or `"streamable_http"`) and `url` field to `McpServerConfig`. Updated `load_mcp_servers()` to parse these from config. Validation: HTTP requires url, stdio requires command. |
| `promaia/mcp/__init__.py` | Removed `McpProtocolClient` from `__all__` (implementation detail, not public API). |
| `promaia/agents/mcp_loader.py` | SDK path now skips servers with `transport=streamable_http` (SDK doesn't support remote MCP). Logs a warning. |

### Files NOT changed (and why)

| File | Why untouched |
|---|---|
| `promaia/mcp/execution.py` | Consumes the dict format that `result_adapter` now produces. Transport-agnostic. |
| `promaia/mcp/tools.py` | Tool registry, transport-agnostic. |
| `promaia/agents/agentic_turn.py` | Doesn't use MCP protocol at all — hardcoded tools with `ToolExecutor`. |
| `promaia/agents/executor.py` | SDK path uses `mcp_loader` output, SDK manages its own MCP connections. |
| `promaia/mcp/query_tools_server.py` | Built-in server, stays on stdio. Out of scope. |
| `promaia/mcp/gmail_tools_server.py` | Built-in server, stays on stdio. Out of scope. |
| `promaia/mcp/calendar_tools_server.py` | Built-in server, stays on stdio. Out of scope. |

## What was verified

- **Stdio end-to-end:** Spawned a minimal MCP test server, connected via `McpProtocolClient`, listed tools, called a tool, got correct result, disconnected cleanly. Also tested via `McpClient` wrapper.
- **HTTP graceful failure:** Attempted HTTP connection to non-existent server. Returns `False`, no crash, no leaked stack traces in application logs.
- **Result adapter:** Tested with real `mcp.types.CallToolResult` objects — normal, error, empty content, and `None` inputs.
- **Import chain:** All modules import cleanly: `result_adapter` -> `protocol` -> `client` -> `__init__`.
- **Prosecheck:** All 3 rules pass.

## What was NOT verified

- No real HTTP server exists yet to test the full HTTP transport end-to-end
- No integration test with `chat/interface.py` (would require starting actual MCP tool servers, which need `anthropic` package in the venv)
- No integration test with `web/routers/mcp.py`
- No test with `notion/prompts.py` MCP path
- The SDK executor path (`executor.py` with `sdk_enabled=True`) was not regression-tested
