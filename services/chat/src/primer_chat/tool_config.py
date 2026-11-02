"""Operator-supplied tool configuration.

Everything here comes from deployment configuration and nothing from a user
or a model. A model cannot ask for a server to be added, a tool to be
allowed, or a sandbox requirement to be waived.

Shell and code execution carry an extra requirement: the operator must
declare, explicitly, that the MCP server providing them runs each session in
an ephemeral sandbox with no host mounts, no secrets, and no network by
default. Primer cannot verify that claim from the outside - what it can do
is refuse to offer such tools when nobody has made it.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: Tools whose whole purpose is running code somebody wrote. These are the
#: ones a sandbox declaration is mandatory for.
EXECUTION_TOOL_PREFIXES = ("shell", "exec", "code", "python", "bash", "sandbox")


class SandboxDeclaration(BaseModel):
    """An operator's statement about how their execution server is isolated.

    Every field defaults to the unsafe answer, so a half-filled declaration
    fails rather than passing by omission. A missing declaration and a
    declaration of "no isolation" are treated the same way: the tools are
    not offered.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    ephemeral_per_session: bool = False
    network_disabled_by_default: bool = False
    no_host_mounts: bool = False
    no_host_socket: bool = False
    no_application_secrets: bool = False
    runs_as_non_root: bool = False
    cpu_limited: bool = False
    memory_limited: bool = False
    disk_limited: bool = False
    process_limited: bool = False
    time_limited: bool = False
    output_limited: bool = False

    @property
    def is_complete(self) -> bool:
        """True only when every guarantee has been declared."""
        return all(getattr(self, name) for name in type(self).model_fields)

    def missing(self) -> tuple[str, ...]:
        return tuple(name for name in type(self).model_fields if not getattr(self, name))


class ToolServer(BaseModel):
    """One configured MCP server and the tools it may expose."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=64)
    url: str = Field(min_length=1)
    #: How the server speaks MCP over HTTP. Streamable HTTP is the current
    #: transport and the default; SSE is the older one, still what some
    #: servers offer.
    transport: Literal["streamable-http", "sse"] = "streamable-http"
    #: An allowlist, never a denylist. A server that gains a tool in a later
    #: release must not gain the right to run it here by default.
    allowed_tools: tuple[str, ...] = ()
    #: Empty means every authenticated user. A named group restricts it.
    required_groups: tuple[str, ...] = ()
    #: Required for execution tools, ignored otherwise.
    sandbox: SandboxDeclaration | None = None

    @model_validator(mode="after")
    def _execution_requires_a_sandbox(self) -> ToolServer:
        """Fail at configuration load, not at the moment a user runs something.

        A deployment that would refuse every shell call at runtime is
        misconfigured now; saying so at startup gives the operator the error
        while they are still looking at the configuration.
        """
        if not self.provides_execution:
            return self
        if self.sandbox is None or not self.sandbox.is_complete:
            missing = self.sandbox.missing() if self.sandbox else ("a sandbox declaration",)
            raise ValueError(
                f"server '{self.name}' exposes execution tools and is missing: "
                + ", ".join(missing)
            )
        return self

    @property
    def provides_execution(self) -> bool:
        return any(is_execution_tool(tool) for tool in self.allowed_tools)


class ToolConfiguration(BaseModel):
    """The whole deployment's tool policy input."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    #: Deny by default. Tool use is off for the deployment until switched on.
    enabled: bool = False
    servers: tuple[ToolServer, ...] = ()
    #: How long an approval is good for. Short, because an approval is
    #: consent to one action at one moment, not a standing permission.
    approval_ttl_seconds: int = Field(default=300, gt=0)

    def server_for(self, tool_name: str) -> ToolServer | None:
        for server in self.servers:
            if tool_name in server.allowed_tools:
                return server
        return None


def is_execution_tool(tool_name: str) -> bool:
    """Whether a tool runs code, judged from its qualified name.

    Deliberately broad and prefix-based. Over-matching costs an operator one
    sandbox declaration; under-matching lets code execution through with no
    isolation guarantee at all.
    """
    lowered = tool_name.lower()
    parts = lowered.replace("/", ".").split(".")
    return any(part.startswith(prefix) for part in parts for prefix in EXECUTION_TOOL_PREFIXES)
