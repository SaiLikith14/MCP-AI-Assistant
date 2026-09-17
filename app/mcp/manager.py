"""Connects to every MCP server declared in mcp_config.json, discovers their
tools at startup, and exposes one unified tool registry + dispatch call for
the agent loop. This is the "dynamic discovery" piece: adding a new tool to
the assistant means adding a server entry to mcp_config.json, not writing
new agent code.
"""
from __future__ import annotations

import json
import logging
import os
import re
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client

logger = logging.getLogger("mcp_manager")

_ENV_VAR_RE = re.compile(r"\$\{(\w+)\}")


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):
        return _ENV_VAR_RE.sub(lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    return value


@dataclass
class RegisteredTool:
    qualified_name: str  # e.g. "web_search__search" -- what Claude sees
    server_name: str
    original_name: str  # the tool's real name on its own server
    description: str
    input_schema: dict


class MCPManager:
    """Owns the lifetime of every MCP client connection."""

    def __init__(self, config_path: Path):
        self._config_path = config_path
        self._stack = AsyncExitStack()
        self._sessions: dict[str, ClientSession] = {}
        self._tools: dict[str, RegisteredTool] = {}

    @property
    def tools(self) -> dict[str, RegisteredTool]:
        return self._tools

    def tool_defs(self) -> list[dict]:
        """Provider-agnostic tool list (name/description/JSON schema). MCP's
        inputSchema is already plain JSON Schema, so it's reused as-is."""
        return [
            {
                "name": t.qualified_name,
                "description": t.description,
                "input_schema": t.input_schema,
            }
            for t in self._tools.values()
        ]

    async def connect_all(self) -> None:
        raw = json.loads(self._config_path.read_text())
        servers: dict[str, dict] = raw.get("mcpServers", {})

        for name, cfg in servers.items():
            if not cfg.get("enabled", True):
                continue
            cfg = _expand_env(cfg)
            try:
                session = await self._connect_one(name, cfg)
            except Exception:
                logger.exception("Failed to connect MCP server '%s' — skipping it", name)
                continue
            self._sessions[name] = session
            await self._register_tools(name, session)

        logger.info(
            "MCP ready: %d server(s), %d tool(s): %s",
            len(self._sessions),
            len(self._tools),
            ", ".join(sorted(self._tools)),
        )

    async def _connect_one(self, name: str, cfg: dict) -> ClientSession:
        transport = cfg.get("transport", "stdio")

        if transport == "stdio":
            params = StdioServerParameters(
                command=cfg["command"],
                args=cfg.get("args", []),
                env={**os.environ, **cfg.get("env", {})},
            )
            read, write = await self._stack.enter_async_context(stdio_client(params))
        elif transport == "http":
            read, write, _ = await self._stack.enter_async_context(
                streamablehttp_client(cfg["url"])
            )
        else:
            raise ValueError(f"Unknown MCP transport '{transport}' for server '{name}'")

        session = await self._stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        return session

    async def _register_tools(self, server_name: str, session: ClientSession) -> None:
        result = await session.list_tools()
        for tool in result.tools:
            qualified = f"{server_name}__{tool.name}"
            self._tools[qualified] = RegisteredTool(
                qualified_name=qualified,
                server_name=server_name,
                original_name=tool.name,
                description=tool.description or "",
                input_schema=tool.inputSchema or {"type": "object", "properties": {}},
            )

    async def call_tool(self, qualified_name: str, arguments: dict) -> str:
        tool = self._tools.get(qualified_name)
        if tool is None:
            return f"Error: unknown tool '{qualified_name}'"

        session = self._sessions[tool.server_name]
        try:
            result = await session.call_tool(tool.original_name, arguments)
        except Exception as exc:  # noqa: BLE001 - surface the failure back to the model
            logger.exception("Tool call failed: %s", qualified_name)
            return f"Error calling {qualified_name}: {exc}"

        parts: list[str] = []
        for block in result.content:
            if getattr(block, "type", None) == "text":
                parts.append(block.text)
            else:
                parts.append(str(block))
        text = "\n".join(parts) or "(tool returned no content)"
        if result.isError:
            return f"Tool error: {text}"
        return text

    async def close(self) -> None:
        await self._stack.aclose()
