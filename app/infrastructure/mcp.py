from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from app.core.config import settings
from app.core.logger import get_logger

logger = get_logger(__name__)


def _tool_to_openai_schema(tool) -> dict[str, Any]:
    input_schema: dict[str, Any] = tool.inputSchema or {}

    if "type" not in input_schema:
        input_schema = { "type": "object", "properties": {}, **input_schema }

    input_schema.setdefault("additionalProperties", False)

    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": (tool.description or "").strip(),
            "parameters": input_schema
        }
    }


class _ServerConnection:
    def __init__(self, url: str) -> None:
        self.url = url
        self._session: ClientSession | None = None
        self._stack: AsyncExitStack | None = None
        self._tools: list[Any] = []

    @property
    def tools(self) -> list[Any]:
        return self._tools

    async def connect(self) -> None:
        self._stack = AsyncExitStack()
        try:
            transport = await self._stack.enter_async_context(
                streamable_http_client(self.url)
            )

            read, write = transport
            self._session = await self._stack.enter_async_context(
                ClientSession(read, write)
            )

            await self._session.initialize()
            result = await self._session.list_tools()
            self._tools = result.tools

            logger.info(
                "[MCP] Connected to %s — %d tool(s) discovered: %s",
                self.url,
                len(self._tools),
                [t.name for t in self._tools]
            )
        except Exception as exc:
            logger.error("[MCP] Failed to connect to %s: %s", self.url, exc)
            await self._cleanup()
            raise

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        if self._session is None:
            raise RuntimeError(f"Not connected to {self.url}")

        logger.info("[MCP] ► Calling tool '%s' on %s with args: %s", name, self.url, arguments)
        result = await self._session.call_tool(name, arguments)

        parts: list[str] = []
        for block in result.content:
            if hasattr(block, "text"):
                parts.append(block.text)
            else:
                parts.append(str(block))

        output = "\n".join(parts)
        logger.info("[MCP] ◄ Tool '%s' result (%d chars): %.200s", name, len(output), output)
        return output

    async def _cleanup(self) -> None:
        if self._stack is not None:
            try:
                await self._stack.aclose()
            except Exception:
                pass

            self._stack = None
            self._session = None

    async def close(self) -> None:
        await self._cleanup()


class MCPRegistry:

    def __init__(self) -> None:
        self._connections: list[_ServerConnection] = []
        self._tool_map: dict[str, _ServerConnection] = {}  # tool name → server

    async def connect(self, urls: list[str] | None = None) -> None:
        targets = urls if urls is not None else settings.mcp_server_url_list

        if not targets:
            logger.warning("[MCP] No MCP server URLs configured — tool use disabled")
            return

        tasks = [self._try_connect(url) for url in targets]
        await asyncio.gather(*tasks)

        self._tool_map = {}
        for conn in self._connections:
            for tool in conn.tools:
                if tool.name in self._tool_map:
                    logger.warning(
                        "[MCP] Duplicate tool name '%s' from %s — using first registration",
                        tool.name,
                        conn.url,
                    )
                else:
                    self._tool_map[tool.name] = conn

        logger.info(
            "[MCP] Registry ready — %d server(s), %d tool(s) total: %s",
            len(self._connections),
            len(self._tool_map),
            list(self._tool_map.keys()),
        )

    async def _try_connect(self, url: str) -> None:
        conn = _ServerConnection(url)

        try:
            await conn.connect()
            self._connections.append(conn)
        except Exception as exc:
            logger.warning("[MCP] Skipping %s after connection error: %s", url, exc)

    async def close(self) -> None:
        for conn in self._connections:
            await conn.close()

        self._connections.clear()
        self._tool_map.clear()

        logger.info("[MCP] All connections closed")


    @property
    def has_tools(self) -> bool:
        return bool(self._tool_map)

    def get_tools_schema(self) -> list[dict[str, Any]]:
        schema: list[dict[str, Any]] = []

        for conn in self._connections:
            for tool in conn.tools:
                schema.append(_tool_to_openai_schema(tool))

        return schema


    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        if name not in self._tool_map:
            raise KeyError(f"Unknown MCP tool: '{name}'. Available: {list(self._tool_map.keys())}")

        conn = self._tool_map[name]
        return await conn.call_tool(name, arguments)

    def get_tool_names(self) -> list[str]:
        return list(self._tool_map.keys())


registry = MCPRegistry()