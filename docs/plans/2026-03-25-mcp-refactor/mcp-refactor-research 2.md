# MCP Transport Migration: stdio → Streamable HTTP

## Status

Proposed

## Date

2026-03-20

## Goal

Replace the hand-rolled JSON-RPC client with the official `mcp.client` and add Streamable HTTP transport support, so users can connect Promaia agents to their own custom MCP servers hosted externally. Built-in tool servers (query_tools, gmail, calendar) stay on stdio — they work fine and are not being migrated.

---

## Decisions

### Use the official `mcp` PyPI package only

We use `from mcp.server.fastmcp import FastMCP` from the `mcp` package (>=1.26, already in requirements). Do **not** install or import from the standalone `fastmcp` PrefectHQ package. They share FastMCP 1.0 lineage but have diverged — different middleware APIs, different internals, different release cadence. Having both in the project guarantees Claude Code will cross-contaminate imports.

### Stateless HTTP mode, JSON responses

All three servers run with `stateless_http=True` and `json_response=True`. Promaia is single-tenant with one agent runner per instance. We don't need sessions, SSE streaming, or server-initiated notifications. Stateless mode avoids session cleanup bugs, DELETE-on-disconnect issues, and memory leaks from idle SSE connections that never send anything.

**Exception**: If any tool needs progress notifications in the future (e.g., long-running email sends), `json_response=True` must be dropped for that server. Decide per-server, not globally.

### API key auth via FastAPI middleware

Tool servers are mounted inside a FastAPI app. A `BaseHTTPMiddleware` subclass checks `X-API-Key` on requests to `/mcp/*` paths. The key is a static environment variable (`MCP_API_KEY`). This is defense-in-depth — servers bind to `127.0.0.1` only. Do not use query param auth, JWT, or OAuth. This is internal-only transport on a single-tenant droplet.

### Keep the Claude Agent SDK executor path on stdio (for now)

`load_mcp_servers_for_agent()` outputs `{command, args, env}` for the Claude Agent SDK. The SDK at v0.1.23 almost certainly doesn't support remote MCP servers. Do not attempt to make it work with HTTP URLs. The SDK path stays on stdio. The custom executor path (non-SDK) migrates to HTTP. Revisit when the SDK adds remote MCP support or when we drop the SDK path entirely.

### Replace the hand-rolled client with `mcp.client`

`promaia/mcp/protocol.py` (~270 lines of custom JSON-RPC-over-stdio) is replaced by the official `mcp.client.ClientSession` with `streamablehttp_client`. Add an adapter layer between the official client's `CallToolResult` type and what `execution.py` expects — do not sprinkle `.content[0].text` throughout the codebase.

### Mount all tool servers on a single FastAPI app

One process, one port, multiple mount paths:

- `/query/mcp` — query_tools
- `/gmail/mcp` — gmail  
- `/calendar/mcp` — calendar

This keeps deployment simple (one service to manage) while maintaining logical separation. Each FastMCP instance has its own session manager and must be wired into the shared lifespan.

---

## Gotchas for Claude Code

### 1. Two packages both export `FastMCP`

The official `mcp` SDK has `mcp.server.fastmcp.FastMCP`. The PrefectHQ standalone has `fastmcp.FastMCP`. Claude Code's training data mixes them freely. It will use `@mcp.middleware("request")` (PrefectHQ-only API) on the official SDK, or import from the wrong package. Pin this in every task prompt.

### 2. Lifespan context is mandatory for Streamable HTTP

`mcp.server.fastmcp.FastMCP` requires its session manager to be started via async context manager. When mounting under FastAPI, the lifespan must use `AsyncExitStack` to enter each server's `session_manager.run()`. If this is missing, requests hang silently with no error. Example pattern:

```python
@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    async with contextlib.AsyncExitStack() as stack:
        await stack.enter_async_context(gmail_mcp.session_manager.run())
        await stack.enter_async_context(calendar_mcp.session_manager.run())
        yield
```

Claude Code will forget this. Verify it's present in every review.

### 3. Server lifecycle changes fundamentally

With stdio, the MCP server dies when the parent kills the subprocess. Process management code that assumes "server lifetime = subprocess lifetime" breaks. HTTP servers run until explicitly shut down. If the agent runner currently restarts tool servers by killing child processes, that pattern must be replaced with HTTP health checks or explicit shutdown endpoints.

### 4. The official `mcp.client` uses nested async context managers

```python
async with streamablehttp_client(url, headers=...) as (read, write, session_id):
    async with ClientSession(read, write) as session:
        await session.initialize()
        # tools only available inside this block
```

If `McpClient.connect_to_server()` stores the session as an instance variable and returns, the context manager scope must be managed externally — use `AsyncExitStack` on the client side too. If `disconnect()` is never called (crash, exception), sessions leak.

### 5. Document all error paths before replacing protocol.py

The hand-rolled client has hardened behavior around process cleanup, timeout handling, retry logic, and error surfacing. The official client behaves differently in all edge cases. Before deleting protocol.py, have Claude Code write a scratch doc listing every error path, timeout, and retry in the existing code. Then verify each has an equivalent in the official client.

### 6. Tool result format differs

The official client returns `CallToolResult` with `.content` as a list of `TextContent`/`ImageContent`/etc. blocks and an `.isError` boolean. The existing execution layer expects plain strings or dicts. Build a single adapter function, not inline conversions. Edge cases to handle: multiple content blocks, `.isError = True`, empty content list.

### 7. Environment variables don't inherit automatically

With stdio, child processes inherit the parent's env. With HTTP, tool servers are separate processes. Every `os.environ`/`os.getenv` call in each server file must be audited. Especially: Google OAuth token file paths (may be relative to parent's cwd), Anthropic API keys, database connection strings. Grep each server for env var access before starting.

### 8. Bind to 127.0.0.1, not 0.0.0.0

On DigitalOcean droplets, binding to `0.0.0.0` without a firewall rule exposes tool endpoints to the internet. Always bind to `127.0.0.1`. Only the local agent runner talks to tool servers.

### 9. Request ordering is not guaranteed

Stdio is a single ordered stream. HTTP requests are concurrent — the server may process them out of order under `stateless_http=True`. If the agent sends sequential tool calls where the second depends on the first's side effect (e.g., "create draft" then "send draft"), race conditions can appear that never existed with stdio. The MCP spec assumes tool calls are independent; verify our tools actually are.

### 10. `mcp` package version matters

Streamable HTTP support landed in v1.8.0 (May 2025). Our pinned `>=1.26.0` is fine, but if Claude Code touches requirements it may downgrade or pin to an older version from its training data. Verify the installed version supports `streamable_http_app()` and `stateless_http=True`.

### 11. Tool list caching

The official client has a `cache_tools_list` parameter. Default behavior and invalidation differ from the hand-rolled client's "call list_tools once at startup" pattern. If we need hot-reload of tools mid-session, caching must be explicitly disabled. If we don't, enable caching to avoid per-step round-trips.

### 12. No test suite means no regression detection

The codebase has no automated tests. Claude Code will verify one happy path and move on. Every edge case (timeouts, server crashes mid-call, malformed tool results, concurrent calls) will be untested. Consider writing 3-5 integration tests exercising the current MCP flow end-to-end **before** the refactor, then verify they pass after.

---

## Scope boundaries (revised 2026-03-25)

The original research explored migrating built-in tool servers to HTTP. **That is no longer the goal.** The actual goal is enabling users to connect Promaia to their own custom MCP servers hosted externally.

### In scope

- Replace `promaia/mcp/protocol.py` with official `mcp.client` — **DONE**
- Update `promaia/mcp/client.py` to support HTTP transport — **DONE**
- Update `promaia/config/mcp_servers.py` with transport/url fields — **DONE**
- Adapter layer for tool result format — **DONE**
- User-facing config for registering external MCP servers — **NOT STARTED**
- Wiring custom server tools into agent execution — **NOT STARTED**

### Out of scope

- **Migrating built-in tool servers (query, gmail, calendar) to HTTP** — they work fine on stdio, leave them alone
- **Creating `http_app.py` or FastAPI wrappers** — no local HTTP servers being created
- **API key middleware for local servers** — not applicable
- Claude Agent SDK executor path (stays on stdio)
- `promaia/agents/agentic_turn.py` ToolExecutor (hardcoded tools, no MCP protocol)
- `promaia/mcp/execution.py` internals (transport-agnostic after adapter)
- Any changes to promaia-ts

### Decisions from this research that still apply

The gotchas in this doc (sections 1-12 above) are still relevant for the client side — especially #4 (nested async context managers / AsyncExitStack), #6 (tool result format adapter), and #11 (tool list caching). The server-side gotchas (#2 lifespan, #7 env vars, #8 bind address) are no longer relevant since we're not creating HTTP servers.

---

## File change map (revised)

| File | Change | Status |
|---|---|---|
| `promaia/mcp/protocol.py` | Rewritten with official `mcp.client` | **Done** |
| `promaia/mcp/client.py` | Supports stdio + HTTP transport branching | **Done** |
| `promaia/config/mcp_servers.py` | `transport` and `url` fields on `McpServerConfig` | **Done** |
| `promaia/mcp/result_adapter.py` | New — converts `CallToolResult` to internal dicts | **Done** |
| `promaia/agents/mcp_loader.py` | Skips HTTP servers for SDK path | **Done** |
| `promaia/mcp/__init__.py` | Removed `McpProtocolClient` from exports | **Done** |

---

## Verification checklist (revised)

- [x] Official client connects to stdio MCP servers and lists/calls tools
- [x] HTTP transport attempt fails gracefully when server unreachable
- [x] Result adapter converts all content types correctly
- [x] SDK path unaffected (mcp_loader skips HTTP servers)
- [x] Prosecheck passes
- [ ] End-to-end test with a real external HTTP MCP server
- [ ] User config flow for adding custom servers
- [ ] Custom server tools available during agent execution
- [ ] Auth headers passed through correctly to external servers
- [ ] No import from `fastmcp` (PrefectHQ package) anywhere in codebase
