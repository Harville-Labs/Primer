"""Running an approved tool call.

Primer never executes anything itself. Tools live behind MCP servers the
operator configured, invoked through Haystack, so the process that runs a
command is one the operator chose and isolated - not this one.

Approval is re-checked immediately before invocation rather than trusted
from when it was granted. An approval that expired while a queue drained is
not an approval, and the gap between deciding and running is exactly where
that matters.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from haystack.tools import Tool
from primer_contracts.chat import ToolPhase

from primer_chat.tool_config import ToolConfiguration
from primer_chat.tool_policy import ApprovalState, ToolPolicy, ToolRequest

logger = logging.getLogger(__name__)

#: Tool output is untrusted text from a process Primer does not control.
#: Truncating it bounds both the audit row and what a model is handed back.
MAX_OUTPUT_CHARS = 8000

#: Argument values are shown to a user for approval and stored in the audit
#: row, so they are bounded too - a tool asked to write a megabyte of input
#: should not put a megabyte in the approval dialog.
MAX_ARGUMENT_CHARS = 2000


class ToolInvoker(Protocol):
    """What the runner needs from Haystack's MCP toolset."""

    def invoke(self, tool_name: str, arguments: dict[str, Any]) -> str: ...


class ToolSource(ToolInvoker, Protocol):
    """The configured servers: what they offer, and a way to call it."""

    def tools(self) -> Sequence[Tool]: ...


@dataclass(frozen=True)
class ToolOutcome:
    """What happened, in terms the audit row and the stream both use."""

    phase: ToolPhase
    output: str | None = None
    error_code: str | None = None
    duration_ms: int | None = None


def sanitize_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    """Bound what is shown and stored, without hiding what is being approved.

    Values are truncated rather than redacted. A user approving "run this
    command" has to see the command; replacing it with a placeholder would
    ask them to consent to something they cannot read.
    """
    cleaned: dict[str, Any] = {}
    for key, value in arguments.items():
        if isinstance(value, str) and len(value) > MAX_ARGUMENT_CHARS:
            cleaned[key] = value[:MAX_ARGUMENT_CHARS] + "… (truncated)"
        else:
            cleaned[key] = value
    return cleaned


class ToolRunner:
    """Invokes approved calls, and refuses everything else."""

    def __init__(
        self,
        configuration: ToolConfiguration,
        policy: ToolPolicy,
        invoker: ToolInvoker | None = None,
    ) -> None:
        self._configuration = configuration
        self._policy = policy
        self._invoker = invoker

    def run(self, request: ToolRequest) -> ToolOutcome:
        """Invoke a call, if it may still run.

        The approval check happens here rather than at the caller, because
        this is the last moment before something executes and the only place
        the answer cannot go stale.
        """
        if request.state is ApprovalState.EXPIRED or request.is_expired():
            return ToolOutcome(phase=ToolPhase.EXPIRED, error_code="approval_expired")
        if not request.may_run():
            return ToolOutcome(phase=ToolPhase.DENIED, error_code="not_approved")
        if self._invoker is None:
            # Configured tools with no MCP connection is a deployment fault,
            # not a user error, and it must not be reported as a refusal.
            return ToolOutcome(phase=ToolPhase.FAILED, error_code="tools_unavailable")

        started = time.monotonic()
        try:
            output = self._invoker.invoke(request.tool_name, request.arguments)
        except Exception:
            # The trace goes to the worker log. A tool's failure message can
            # contain anything the tool chose to print, so only a stable code
            # reaches the user.
            logger.exception("tool call %s failed", request.id)
            return ToolOutcome(
                phase=ToolPhase.FAILED,
                error_code="tool_failed",
                duration_ms=int((time.monotonic() - started) * 1000),
            )

        return ToolOutcome(
            phase=ToolPhase.COMPLETED,
            output=truncate(output),
            duration_ms=int((time.monotonic() - started) * 1000),
        )


def truncate(output: str) -> str:
    if len(output) <= MAX_OUTPUT_CHARS:
        return output
    return output[:MAX_OUTPUT_CHARS] + "\n… (output truncated)"


def build_invoker(configuration: ToolConfiguration) -> ToolSource | None:
    """Connect to the configured MCP servers, if any are configured.

    Imported lazily: a deployment with tools switched off should not pay for
    Haystack's MCP integration at startup, and should not fail to start
    because an optional dependency is missing.
    """
    if not configuration.enabled or not configuration.servers:
        return None
    try:
        from primer_chat.mcp import HaystackToolInvoker
    except ImportError:
        logger.warning("tools are enabled but the MCP integration is not installed")
        return None
    return HaystackToolInvoker(configuration)
