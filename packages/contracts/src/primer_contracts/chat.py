"""Chat, citation, and streaming contracts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import ConfigDict, Field

from primer_contracts.base import WireModel
from primer_contracts.identity import Principal
from primer_contracts.retrieval import SourceLocator

Message = Annotated[str, Field(min_length=1, max_length=32000)]


class Citation(WireModel):
    """A grounded reference from an assistant response to a source passage.

    Citations address an immutable document version so a later replacement
    cannot silently change what an existing answer claimed to cite.
    """

    document_id: UUID
    document_version_id: UUID
    chunk_id: UUID
    locator: SourceLocator | None = None
    excerpt: str | None = Field(default=None, max_length=2000)


class ChatRequest(WireModel):
    """A user turn, optionally scoped to one of the principal's libraries.

    Without a library there is nothing to retrieve, so the answer comes from
    the model alone and carries no citations. That is a different kind of
    answer rather than a lesser one, and the absence of `library_id` is what
    says so: a caller cannot ask for an ungrounded answer and a cited one at
    the same time.
    """

    principal: Principal
    library_id: UUID | None = None
    #: Which model to answer with. One the deployment offers, or none for its
    #: default. Never an arbitrary name: what an endpoint happens to serve is
    #: not the same as what an operator chose to expose here.
    model: str | None = Field(default=None, max_length=200)
    message: Message
    conversation_id: UUID | None = None
    tools_enabled: bool = False


class ChatModel(WireModel):
    """One model a user may pick between.

    Named separately from the provider's own listing: an endpoint serves what
    it serves, and this is the subset an operator decided to offer.
    """

    id: str = Field(min_length=1, max_length=200)
    #: True for the one used when a request expresses no preference.
    default: bool = False
    #: Which provider serves it. A deployment can hold several at once, and
    #: model names are not unique across them - two endpoints serving
    #: `llama3.1:8b` is the ordinary case, not a corner one - so a model is
    #: only fully named by the pair. A request that gives a model without a
    #: provider is resolved against the first that serves it.
    provider_id: UUID | None = None
    #: The operator's label for that provider, so a picker can say where a
    #: model runs without a second lookup. A hostname would not do: what a
    #: user needs to tell apart is "my machine" from "the paid one".
    provider_name: str | None = Field(default=None, max_length=80)
    #: What the provider declared it returns, so a page that scores text can
    #: offer only what will be answered. On the model rather than read from
    #: the provider list, which is for administrators.
    supports_logprobs: bool = False
    supports_prompt_logprobs: bool = False


class ChatModelList(WireModel):
    """What this deployment can answer with, and whether it could be asked.

    An empty list with `endpoint_reachable` false is not the same fact as an
    empty list with it true, and the difference is the whole point of the
    field. Primer used to answer an unreachable endpoint with its configured
    default name, which put a model in the picker that nothing was serving:
    the interface offered a choice that did not exist and the failure only
    surfaced when someone asked a question and waited for an answer that was
    never coming.
    """

    models: tuple[ChatModel, ...] = ()
    #: Whether any enabled provider answered. False means nothing here can be
    #: asked, and the interface must say so rather than offer a model. With
    #: several providers this is true when any one of them replied: the
    #: deployment can still answer, and the ones that did not are reported
    #: individually in `unreachable`.
    endpoint_reachable: bool = True
    #: What went wrong, for an operator reading it. Never a stack trace.
    detail: str | None = Field(default=None, max_length=500)
    #: Providers that did not answer, by name, while others did. A partial
    #: outage is not the same as an outage, and a deployment that quietly
    #: dropped a provider from the picker would look like one that had never
    #: been configured with it.
    unreachable: tuple[str, ...] = ()


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class MessageState(StrEnum):
    """A message is always in exactly one of these.

    `STREAMING` is the only non-terminal state. A message left in it is a
    stream that died, which is a recoverable fault rather than an answer, and
    is distinguishable from one that genuinely failed.
    """

    STREAMING = "streaming"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ConversationSummary(WireModel):
    """A conversation, which belongs to one person and at most one library.

    A conversation with no library is ungrounded for its whole length: the
    library is fixed when it starts, so citations in one turn and none in the
    next cannot happen within a single thread.
    """

    id: UUID
    library_id: UUID | None = None
    owner_user_id: UUID
    title: str = Field(max_length=200)
    created_at: datetime
    updated_at: datetime


class MessageSummary(WireModel):
    """One turn, and what it was grounded in.

    Citations are stored with the message rather than recomputed, because
    what an answer cited is a fact about that answer. Re-retrieving later
    would produce today's passages for yesterday's words.

    Content keeps its whitespace, like `MessageDelta`, so that concatenating
    a stream's fragments yields exactly the stored answer. Trimming here
    would make the two disagree for any answer that begins or ends with
    space, and a client comparing them would be right to think it had lost
    something.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=False)

    id: UUID
    conversation_id: UUID
    role: MessageRole
    state: MessageState
    content: str
    #: What the model worked through before answering, for a model that
    #: shows it. Stored beside the answer rather than mixed into it: it is
    #: scratch work, it is not what the answer says, and a reader who copies
    #: an answer must not carry it away with them.
    #:
    #: None means a model that does not reason aloud, which is most of them.
    #: Empty means one that does and had nothing to say this turn.
    reasoning: str | None = Field(default=None)
    citations: tuple[Citation, ...] = ()
    #: Which model produced this, recorded per message. A deployment can
    #: offer several and a user can switch between turns, so this is part of
    #: the answer rather than a property of the deployment as it is today.
    provider_model: str | None = Field(default=None, max_length=200)
    #: Which provider served that model. Recorded beside the name because a
    #: name alone cannot say where a question should go: two endpoints
    #: serving `llama3.1:8b` is the ordinary case, not a corner one. Null for
    #: an answer written before a deployment could hold several, and for one
    #: answered by the endpoint configured for the deployment itself.
    provider_id: UUID | None = Field(default=None)
    error_code: str | None = Field(default=None, max_length=64)
    created_at: datetime


class StreamEvent(WireModel):
    """Base for everything sent over SSE.

    Every event carries a monotonic id within its stream, so a client that
    reconnects can say what it already saw, and one that receives events out
    of order can tell.
    """

    id: int = Field(ge=0)


class MessageStarted(StreamEvent):
    type: Literal["message.started"] = "message.started"
    message_id: UUID
    conversation_id: UUID


class MessageDelta(StreamEvent):
    """A fragment of the answer, exactly as the model produced it.

    Whitespace is deliberately not stripped here, unlike every other wire
    string. Fragments are concatenated by the client, and the space between
    two words routinely arrives as the leading or trailing character of a
    fragment - stripping it silently welds words together in the reader's
    copy while the stored answer looks fine.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=False)

    type: Literal["message.delta"] = "message.delta"
    text: str


class ReasoningDelta(StreamEvent):
    """A fragment of the model's thinking, on its own channel.

    Separate from `MessageDelta` rather than a flag on it, so a client that
    predates reasoning renders the answer correctly and simply ignores this:
    an unknown event is skipped, whereas an unknown field on a known event
    would have been concatenated into the answer.

    Whitespace is preserved for the same reason as `MessageDelta` - these
    fragments are concatenated too.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=False)

    type: Literal["reasoning.delta"] = "reasoning.delta"
    text: str


class CitationEvent(StreamEvent):
    """A source the answer is grounded in.

    Sent as the context is assembled, before any text, so a reader can see
    what the answer is being drawn from while it is still being written.
    """

    type: Literal["citation"] = "citation"
    index: int = Field(ge=1)
    citation: Citation


class MessageCompleted(StreamEvent):
    type: Literal["message.completed"] = "message.completed"
    message: MessageSummary


class StreamError(StreamEvent):
    """A terminal failure, carrying a stable code and nothing else.

    Never a stack trace and never the prompt: a stream is the one place
    where an unfiltered exception would be shown directly to a user.
    """

    type: Literal["error"] = "error"
    code: str = Field(min_length=1, max_length=64)
    detail: str | None = Field(default=None, max_length=2000)


class Heartbeat(StreamEvent):
    """Keeps an idle connection open through proxies that time it out."""

    type: Literal["heartbeat"] = "heartbeat"


class ToolPhase(StrEnum):
    """The stages a tool call passes through, as a client sees them."""

    REQUESTED = "requested"
    APPROVED = "approved"
    DENIED = "denied"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"


class ToolCallSummary(WireModel):
    """One tool call, and what was decided about it.

    Arguments are shown to the user because they are what is being consented
    to: approving "run a command" without seeing the command is not consent.
    They are sanitized before they get here.
    """

    id: UUID
    conversation_id: UUID
    tool_name: str = Field(min_length=1, max_length=200)
    server_name: str = Field(min_length=1, max_length=64)
    arguments: dict[str, Any] = Field(default_factory=dict)
    phase: ToolPhase
    requested_at: datetime
    expires_at: datetime
    decided_at: datetime | None = None
    decided_by: str | None = None
    #: Bounded and redacted. A tool's output is untrusted text from a process
    #: Primer does not control, so it is stored truncated and never executed.
    output: str | None = Field(default=None, max_length=8000)
    error_code: str | None = Field(default=None, max_length=64)
    duration_ms: int | None = Field(default=None, ge=0)


class ToolRequested(StreamEvent):
    """A model asked for a tool. Nothing has run."""

    type: Literal["tool.requested"] = "tool.requested"
    call: ToolCallSummary


class ToolDecided(StreamEvent):
    """A person approved or denied it."""

    type: Literal["tool.decided"] = "tool.decided"
    call: ToolCallSummary


class ToolRunning(StreamEvent):
    type: Literal["tool.running"] = "tool.running"
    call_id: UUID


class ToolOutput(StreamEvent):
    """What the tool returned, bounded.

    Whitespace is preserved for the same reason as message deltas: tool
    output is frequently indented or tabular, and stripping it makes the
    result unreadable.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=False)

    type: Literal["tool.output"] = "tool.output"
    call_id: UUID
    text: str


class ToolCompleted(StreamEvent):
    type: Literal["tool.completed"] = "tool.completed"
    call: ToolCallSummary
