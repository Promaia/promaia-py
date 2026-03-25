# MCP Refactor: Next Steps

**Date:** 2026-03-25
**Prerequisite reading:** `status.md` and `architecture.md` in this directory.

## Goal

Enable users to connect Promaia agents to their own custom MCP servers hosted externally via Streamable HTTP. The client-side transport support is done — the remaining work is configuration UX, agent wiring, and testing.

## Explicitly out of scope

**Do NOT convert Promaia's built-in tool servers (query_tools, gmail, calendar) to HTTP.** They work fine on stdio. The goal is connecting to *user-provided external* MCP servers, not changing how built-in servers run. Do not create `http_app.py`, do not add FastAPI wrappers around existing servers, do not touch the server files.

Also out of scope:
- Changes to `agentic_turn.py` ToolExecutor (hardcoded tools, no MCP protocol)
- Changes to the Claude Agent SDK executor path (SDK manages its own stdio connections)
- Auth middleware for local servers (no local HTTP servers being created)

## Remaining work

### 1. User-facing configuration for custom MCP servers

Users need a way to register their external MCP servers. The config model already supports it — `McpServerConfig` has `transport` and `url` fields. What's missing:

- **Config entry point:** How does a user add a server? Options: `maia mcp add` CLI command, editing `mcp_servers.json` directly, web UI form. Decide which.
- **Per-workspace or global:** Can different workspaces have different custom servers? Currently `mcp_servers.json` is global.
- **Auth headers:** The `connect()` method accepts `headers` (passed through to `streamablehttp_client`). Users need a way to configure auth headers (API keys, bearer tokens) per server. This should go through the auth module or a secure config field — not plaintext in `mcp_servers.json`.

Example config entry for a user's custom server:
```json
"my-custom-tools": {
  "description": "My company's internal tools",
  "transport": "streamable_http",
  "url": "https://mcp.example.com/tools",
  "headers": {"Authorization": "Bearer ${MY_MCP_TOKEN}"},
  "enabled": true
}
```

The `${VAR}` env var expansion already works in `mcp_loader.py`'s `_resolve_env_value()`.

### 2. Wire custom servers into agent tool discovery

The chat interface (`chat/interface.py`) already connects to MCP servers via `McpClient` and injects discovered tools into the system prompt. Custom HTTP servers should flow through the same path — `load_mcp_servers()` reads config, `McpClient.connect_to_server()` connects (now supports HTTP), tools appear.

Verify this works end-to-end with a real external MCP server. The plumbing should be there but hasn't been tested with a live HTTP endpoint.

### 3. Wire custom servers into the non-SDK executor path

`AgentExecutor` has a legacy (non-SDK) path that delegates to `agentic_turn()`. If users configure custom MCP servers for an agent, those tools need to be available during execution. Currently `agentic_turn()` only uses hardcoded `ToolExecutor` — it has no concept of dynamically discovered MCP tools.

Options:
- Add MCP tool discovery to `agentic_turn()` so it can call tools on connected MCP servers
- Or route custom-server agents through the SDK path only (but SDK doesn't support HTTP either)

This is the main design question to resolve.

### 4. Handle connection lifecycle for HTTP servers

Stdio servers die when the subprocess exits. HTTP servers are external — they may be down, slow, or return errors. Need to handle:
- **Connection timeout:** Already supported (`timeout` param in `connect()`), default is sensible
- **Server unavailable at startup:** `connect_to_server()` returns `False`, already handled gracefully
- **Server dies mid-session:** `call_tool()` will raise — needs error handling in the execution layer
- **Reconnection:** Should the client retry on failure? Or just report the error and move on?

### 5. Fix the broken web API endpoints

`web/routers/mcp.py` has `/mcp/execute` and `/mcp/search` endpoints that call `client.call_tool()` on `McpClient`. This method does not exist — it's on `McpProtocolClient`. Pre-existing bug, but relevant since these endpoints would be the natural way to expose custom MCP tools via the web UI.

**Fix:** Add `call_tool(server_name, tool_name, arguments)` to `McpClient` that looks up the protocol client from `self.connected_servers[server_name]` and delegates.

## Design decisions to preserve

1. **`result_adapter.py` is the single conversion layer** — do not sprinkle `.content[0].text` throughout the codebase
2. **`BaseException` catch in `protocol.py`** — needed because anyio's `BaseExceptionGroup` isn't caught by `except Exception`
3. **SDK path stays stdio-only** — `mcp_loader.py` skips HTTP servers for the SDK with a warning
4. **Use `mcp` package only** — never `from fastmcp import FastMCP` (PrefectHQ standalone package)
5. **Built-in tool servers stay on stdio** — they are not being migrated to HTTP

## Testing plan

- Point `McpClient` at a real external MCP server over HTTP and verify tool discovery + execution
- Verify stdio path still works (regression) for built-in servers
- Verify `mcp_loader.py` correctly skips HTTP servers when building SDK configs
- Verify auth headers are passed through to the HTTP transport
- Verify graceful behavior when an external server is unreachable
