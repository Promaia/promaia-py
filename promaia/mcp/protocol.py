"""
MCP protocol client backed by the official ``mcp`` library.

Supports both **stdio** (local subprocess) and **Streamable HTTP** (remote)
transports.  The public interface (connect / list_tools / call_tool /
disconnect / is_connected / get_server_info / get_capabilities) is unchanged
from the previous hand-rolled implementation so that ``client.py`` and
``execution.py`` continue to work without modification.
"""
import contextlib
import logging
import os
import sys
from datetime import timedelta
from typing import Any, Dict, List, Optional

import anyio

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.types import Implementation

try:  # Prefer the non-deprecated transport when available
    from mcp.client.streamable_http import streamable_http_client as _streamable_http_client  # type: ignore
    _HAS_NEW_HTTP_CLIENT = True
except ImportError:  # pragma: no cover — older mcp library
    from mcp.client.streamable_http import streamablehttp_client as _streamable_http_client  # type: ignore
    _HAS_NEW_HTTP_CLIENT = False

try:
    from mcp.shared._httpx_utils import create_mcp_http_client
except ImportError:  # pragma: no cover
    create_mcp_http_client = None  # type: ignore

import httpx

from .result_adapter import adapt_call_tool_result, adapt_tool_list

logger = logging.getLogger(__name__)

_CLIENT_INFO = Implementation(name="promaia-mcp-client", version="2.0.0")


class McpProtocolClient:
    """MCP client using the official ``mcp`` library.

    Wraps ``mcp.client.session.ClientSession`` and manages the async context
    manager stack that keeps the underlying transport alive for the lifetime
    of the connection.
    """

    def __init__(self) -> None:
        self._stack: Optional[contextlib.AsyncExitStack] = None
        self._session: Optional[ClientSession] = None
        self.initialized: bool = False
        self.server_info: Optional[Dict[str, Any]] = None
        self.capabilities: Optional[Dict[str, Any]] = None

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    async def connect(
        self,
        command: Optional[List[str]] = None,
        args: Optional[List[str]] = None,
        working_dir: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        *,
        transport: str = "stdio",
        url: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: int = 30,
    ) -> bool:
        """Connect to an MCP server.

        For **stdio** transport, *command* (+ optional *args*) is required.
        For **streamable_http** transport, *url* is required.

        Returns True on success, False on failure.
        """
        try:
            self._stack = contextlib.AsyncExitStack()
            await self._stack.__aenter__()

            if transport == "streamable_http":
                if not url:
                    logger.error("URL required for streamable_http transport")
                    await self._cleanup_stack()
                    return False
                session = await self._connect_http(url, headers=headers, timeout=timeout)
            else:
                if not command:
                    logger.error("Command required for stdio transport")
                    await self._cleanup_stack()
                    return False
                session = await self._connect_stdio(command, args, working_dir, env)

            if session is None:
                await self._cleanup_stack()
                return False

            self._session = session

            # Initialize the session (MCP handshake). The underlying
            # streamable_http transport has its own cancel scope that
            # cancels on any downstream error; wrap with fail_after so a
            # slow/cold server produces a clear timeout instead of a bare
            # "Cancelled by cancel scope" message.
            try:
                with anyio.fail_after(timeout):
                    init_result = await self._session.initialize()
            except TimeoutError:
                logger.error(
                    "MCP handshake timed out after %ss (transport=%s, url=%s). "
                    "Server may be cold-starting, unreachable, or not speaking "
                    "the MCP streamable-HTTP protocol at this path.",
                    timeout,
                    transport,
                    url if transport == "streamable_http" else "—",
                )
                await self._cleanup_stack()
                return False

            self.initialized = True
            self.server_info = {
                "name": init_result.serverInfo.name if init_result.serverInfo else "unknown",
                "version": getattr(init_result.serverInfo, "version", None),
            }
            self.capabilities = (
                init_result.capabilities.model_dump() if init_result.capabilities else {}
            )

            logger.info(
                "Connected to MCP server: %s (transport=%s)",
                self.server_info.get("name"),
                transport,
            )
            return True

        except BaseException as e:
            # A bare "Cancelled by cancel scope" here almost always means the
            # handshake task was torn down by the streamable-HTTP transport's
            # task group after some downstream error (server returned HTML,
            # 404, or never completed the MCP initialize exchange). Log the
            # endpoint explicitly so the user can diagnose without re-running.
            target = url if transport == "streamable_http" else " ".join(command or [])
            logger.error(
                "Error connecting to MCP server (transport=%s, target=%s): %s",
                transport,
                target,
                e,
            )
            await self._cleanup_stack()
            return False

    async def _connect_stdio(
        self,
        command: List[str],
        args: Optional[List[str]],
        working_dir: Optional[str],
        env: Optional[Dict[str, str]],
    ) -> Optional[ClientSession]:
        """Open a stdio transport and return a ClientSession."""
        full_args = (command[1:] if len(command) > 1 else []) + (args or [])

        # Merge env: parent env + caller overrides
        merged_env: Optional[Dict[str, str]] = None
        if env:
            merged_env = {**os.environ, **env}

        params = StdioServerParameters(
            command=command[0],
            args=full_args,
            env=merged_env,
            cwd=working_dir,
        )

        logger.info("Starting MCP server (stdio): %s %s", command[0], " ".join(full_args))

        read_stream, write_stream = await self._stack.enter_async_context(
            stdio_client(params, errlog=sys.stderr)
        )
        session = await self._stack.enter_async_context(
            ClientSession(read_stream, write_stream, client_info=_CLIENT_INFO)
        )
        return session

    async def _connect_http(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        timeout: int = 30,
    ) -> Optional[ClientSession]:
        """Open a Streamable HTTP transport and return a ClientSession.

        Uses the non-deprecated `streamable_http_client` when present, with
        an httpx client that carries caller-supplied headers and honors the
        requested timeout. Falls back to the legacy `streamablehttp_client`
        signature when running against an older `mcp` package.
        """
        logger.info("Connecting to MCP server (HTTP): %s", url)

        # Pre-flight: surface auth / 404 / wrong-endpoint errors cleanly. The
        # streamable-HTTP transport wraps these in a task-group cancel, which
        # ends up as "Cancelled by cancel scope <id>" — useless for debugging.
        # A direct POST of a valid MCP initialize request lets us see the
        # real status code.
        diag_err = await self._preflight_http(url, headers, timeout)
        if diag_err:
            logger.error("MCP pre-flight failed for %s: %s", url, diag_err)
            return None

        if _HAS_NEW_HTTP_CLIENT and create_mcp_http_client is not None:
            # New API: pass a fully configured httpx client so we can thread
            # headers + timeout through without deprecated kwargs.
            http_timeout = httpx.Timeout(timeout, read=max(timeout, 300))
            client = create_mcp_http_client(
                headers=headers or None,
                timeout=http_timeout,
            )
            read_stream, write_stream, _get_session_id = await self._stack.enter_async_context(
                _streamable_http_client(url, http_client=client)
            )
        else:  # pragma: no cover — legacy path
            read_stream, write_stream, _get_session_id = await self._stack.enter_async_context(
                _streamable_http_client(
                    url,
                    headers=headers,
                    timeout=timedelta(seconds=timeout),
                )
            )
        session = await self._stack.enter_async_context(
            ClientSession(read_stream, write_stream, client_info=_CLIENT_INFO)
        )
        return session

    async def _preflight_http(
        self,
        url: str,
        headers: Optional[Dict[str, str]],
        timeout: int,
    ) -> Optional[str]:
        """Probe the MCP HTTP endpoint with a real initialize request.

        Returns ``None`` when the endpoint looks usable (2xx, or an SSE /
        JSON response). Returns a human-readable error string otherwise so
        the caller can log a clear reason (401, 404, plain HTML, etc).
        """
        probe_headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        if headers:
            probe_headers.update(headers)

        init_payload = {
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {
                    "name": _CLIENT_INFO.name,
                    "version": _CLIENT_INFO.version,
                },
            },
        }

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(timeout),
                follow_redirects=True,
            ) as client:
                resp = await client.post(url, json=init_payload, headers=probe_headers)
        except httpx.HTTPError as e:
            return f"network error: {e}"

        if resp.status_code == 401:
            return (
                "server returned 401 Unauthorized — this MCP server requires "
                "authentication. Set an Authorization header in the server's "
                "`env` config (e.g. `\"Authorization\": \"Bearer ${MRP_TOKEN}\"`)."
            )
        if resp.status_code == 403:
            return "server returned 403 Forbidden — credentials are rejected."
        if resp.status_code == 404:
            return (
                "server returned 404 Not Found — the URL path is probably "
                "wrong (common mistake: using the landing page instead of "
                "the `/mcp` or `/sse` endpoint)."
            )
        if resp.status_code >= 500:
            return f"server returned HTTP {resp.status_code} — upstream is unhealthy."
        if resp.status_code >= 400:
            return f"server returned HTTP {resp.status_code}."

        ctype = resp.headers.get("content-type", "")
        if "html" in ctype.lower():
            return (
                "server returned HTML instead of MCP JSON/SSE — the URL is "
                "probably not an MCP endpoint (did you paste the web UI "
                "URL by mistake?)."
            )

        return None

    # ------------------------------------------------------------------
    # Tool discovery & execution
    # ------------------------------------------------------------------

    async def list_tools(self) -> Optional[List[Dict[str, Any]]]:
        """List available tools.  Returns list of dicts or None on failure."""
        if not self.initialized or not self._session:
            logger.error("Client not initialized")
            return None
        try:
            result = await self._session.list_tools()
            tools = adapt_tool_list(result)
            logger.info("Retrieved %d tools from MCP server", len(tools))
            return tools
        except Exception as e:
            logger.error("Error listing tools: %s", e)
            return None

    async def call_tool(
        self, tool_name: str, arguments: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Call a tool.  Returns result dict or None on failure.

        The returned dict has the shape ``{"content": [...], "isError": bool}``
        matching what ``execution.py`` expects.
        """
        if not self.initialized or not self._session:
            logger.error("Client not initialized")
            return None
        try:
            raw = await self._session.call_tool(tool_name, arguments)
            return adapt_call_tool_result(raw)
        except Exception as e:
            logger.error("Error calling tool '%s': %s", tool_name, e)
            return None

    # ------------------------------------------------------------------
    # Lifecycle helpers
    # ------------------------------------------------------------------

    async def disconnect(self) -> None:
        """Disconnect and clean up all resources."""
        await self._cleanup_stack()

    async def _cleanup_stack(self) -> None:
        if self._stack is not None:
            try:
                await self._stack.aclose()
            except BaseException as e:
                # BaseExceptionGroup from anyio can occur during cleanup
                logger.debug("Error during disconnect cleanup: %s", e)
            finally:
                self._stack = None
                self._session = None
                self.initialized = False
                self.server_info = None
                self.capabilities = None

    def is_connected(self) -> bool:
        return self._session is not None and self.initialized

    def get_server_info(self) -> Optional[Dict[str, Any]]:
        return self.server_info

    def get_capabilities(self) -> Optional[Dict[str, Any]]:
        return self.capabilities
