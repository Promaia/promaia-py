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

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamablehttp_client
from mcp.types import Implementation

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

            # Initialize the session (MCP handshake)
            init_result = await self._session.initialize()

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
            logger.error("Error connecting to MCP server: %s", e)
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
        """Open a Streamable HTTP transport and return a ClientSession."""
        logger.info("Connecting to MCP server (HTTP): %s", url)

        read_stream, write_stream, _get_session_id = await self._stack.enter_async_context(
            streamablehttp_client(url, headers=headers, timeout=timedelta(seconds=timeout))
        )
        session = await self._stack.enter_async_context(
            ClientSession(read_stream, write_stream, client_info=_CLIENT_INFO)
        )
        return session

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
