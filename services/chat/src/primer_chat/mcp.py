"""The bridge to Haystack's MCP integration.

Kept in its own module so the rest of Chat can be imported, tested, and run
without the MCP dependency present. A deployment with tools switched off
never touches this file.
"""

from __future__ import annotations

import logging
from typing import Any

from haystack.tools import Tool
from haystack_integrations.tools.mcp import (
    MCPError,
    MCPServerInfo,
    MCPToolset,
    SSEServerInfo,
    StreamableHttpServerInfo,
)

from primer_chat.tool_config import ToolConfiguration, ToolServer

logger = logging.getLogger(__name__)


def _server_info(server: ToolServer) -> MCPServerInfo:
    if server.transport == "sse":
        return SSEServerInfo(url=server.url)
    return StreamableHttpServerInfo(url=server.url)


class HaystackToolInvoker:
    """Lists and invokes MCP tools through Haystack.

    Primer runs no subprocesses of its own. Every tool executes inside the
    MCP server the operator configured, which is where the sandbox
    guarantees they declared actually live.

    Servers are connected on first use rather than at startup. A search
    server that is down when Chat starts should cost the turns that would
    have searched, not every turn: connecting eagerly would fail the whole
    service over one optional dependency.
    """

    def __init__(self, configuration: ToolConfiguration) -> None:
        self._configuration = configuration
        self._toolsets = {
            server.name: MCPToolset(
                server_info=_server_info(server),
                # The allowlist is passed to the toolset as well as checked
                # by the policy. Two places, deliberately: this one stops a
                # tool being connected at all, the other stops it being run.
                tool_names=list(server.allowed_tools),
            )
            for server in configuration.servers
        }

    def _connected(self, name: str) -> MCPToolset | None:
        """A server's toolset, connecting it if it is not yet.

        None when it cannot be reached. Tried again on the next turn, since
        `warm_up` only remembers a connection that succeeded.
        """
        toolset = self._toolsets[name]
        try:
            toolset.warm_up()
        except MCPError:
            # Unreachable, or reachable but missing a tool the allowlist
            # names: either way the operator's configuration is not what is
            # running, and the turn goes on without it.
            logger.warning("tool server %s is unreachable; its tools are not offered", name)
            return None
        return toolset

    def tools(self) -> list[Tool]:
        """Every allowed tool on every server that answers."""
        offered: list[Tool] = []
        for name in self._toolsets:
            toolset = self._connected(name)
            if toolset is not None:
                offered.extend(toolset.tools)
        return offered

    def invoke(self, tool_name: str, arguments: dict[str, Any]) -> str:
        server = self._configuration.server_for(tool_name)
        if server is None:
            # Unreachable through the policy, which refuses unknown tools
            # first. Raised rather than defaulted so a future caller that
            # skipped the policy fails loudly instead of executing.
            raise LookupError(f"no configured server exposes {tool_name}")

        toolset = self._connected(server.name)
        if toolset is None:
            raise ConnectionError(f"{server.name} is unreachable")
        tool = next((candidate for candidate in toolset.tools if candidate.name == tool_name), None)
        if tool is None:
            raise LookupError(f"{server.name} does not expose {tool_name}")
        return str(tool.invoke(**arguments))
