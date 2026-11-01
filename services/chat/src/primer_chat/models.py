"""Chat-owned SQLAlchemy models.

Chat keeps its tables in their own schema. It shares a PostgreSQL instance
with Control but not a migration history, so neither service's Alembic run
touches the other's tables and the two can be released separately.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from primer_contracts.chat import MessageRole, MessageState, ToolPhase
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

CHAT_SCHEMA = "chat"

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

ROLES = tuple(role.value for role in MessageRole)
STATES = tuple(state.value for state in MessageState)


class Base(DeclarativeBase):
    metadata = MetaData(schema=CHAT_SCHEMA, naming_convention=NAMING_CONVENTION)


class Conversation(Base):
    """A thread of questions about one library.

    `owner_user_id` and `library_id` are stored rather than looked up,
    because every request re-authorizes against Control anyway: these are
    what the request is checked against, not what it is trusted on.

    There is no foreign key to Control's tables. Chat has its own schema and
    no business enforcing another service's referential integrity; a deleted
    library stops answering because Control says so, not because a cascade
    reached in here.
    """

    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    #: Nullable: a conversation with no library is answered by the model
    #: alone, without retrieval and without citations. Fixed for the life of
    #: the thread, so an answer's kind cannot change between turns.
    library_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), index=True)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    #: What is remembered of the turns that no longer fit in the model's
    #: context window. Stored rather than recomputed: it costs a model call
    #: to write, and rewriting it every turn would cost more with every turn.
    summary: Mapped[str | None] = mapped_column(Text)
    #: How far through the conversation the summary reaches. Messages after
    #: it are replayed in full; messages up to it are what the summary is
    #: standing in for. Null means nothing has been compacted yet, which is
    #: different from a summary that covers position zero.
    summary_through_ordinal: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Provider(Base):
    """An inference endpoint an administrator added through the settings page.

    Only the added ones. The provider configured in the chart lives in the
    environment and is reported alongside these rather than written here: a
    row standing in for it would be a second place a deployment's own
    configuration could be changed, and the two could then disagree.

    The API key is stored encrypted and is never returned by any route. What
    a caller can learn is whether one is held.
    """

    __tablename__ = "providers"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    #: The operator's own label, and what a user sees beside a model name.
    #: Unique, because it is how a person tells two endpoints apart and two
    #: called "Local" would make the choice meaningless.
    name: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    base_url: Mapped[str] = mapped_column(String(2000), nullable=False)
    #: Ciphertext, or null for an endpoint that needs no key. Never the key.
    api_key_sealed: Mapped[str | None] = mapped_column(Text)
    #: Disabled rather than deleted is the usual way to take an endpoint out
    #: of service: it keeps the URL and the key for when it comes back.
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    #: Declared by the administrator, never probed. See `ProviderSummary`.
    supports_logprobs: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    supports_prompt_logprobs: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Message(Base):
    """One turn. Content is immutable once the turn reaches a terminal state.

    A message left in `streaming` is a stream that died rather than an
    answer that failed, and the two are kept distinguishable: one is a
    recoverable fault, the other is something the user needs to be told.
    """

    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint(
            "role IN (" + ", ".join(f"'{role}'" for role in ROLES) + ")", name="role_known"
        ),
        CheckConstraint(
            "state IN (" + ", ".join(f"'{state}'" for state in STATES) + ")", name="state_known"
        ),
        # Every read of this table is one conversation in order, so the
        # index is the conversation and the order together. It replaces a
        # plain index on the conversation, which it already covers.
        Index("ix_messages_conversation_id_ordinal", "conversation_id", "ordinal"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(f"{CHAT_SCHEMA}.conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: Position within the conversation, from zero. The order of a
    #: conversation, and not derivable from `created_at`: a question and its
    #: answer are written in one transaction and share that timestamp to the
    #: microsecond.
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    #: What a reasoning model worked through before answering. Nullable with
    #: no default, because the three states differ: null is a model that does
    #: not reason aloud, empty is one that does and said nothing this turn.
    reasoning: Mapped[str | None] = mapped_column(Text)
    #: Which endpoint and model produced this, recorded per message: a
    #: deployment can change models between turns, and an answer's provenance
    #: is part of the answer.
    provider_model: Mapped[str | None] = mapped_column(String(200))
    #: Which provider served it. Not a foreign key: a provider an
    #: administrator has since deleted must not take the record of what
    #: answered a question down with it, and this is a note about the past
    #: rather than a pointer to something that has to still exist.
    provider_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class MessageCitation(Base):
    """What an answer was grounded in, recorded with the answer.

    Stored rather than recomputed: what a reply cited is a fact about that
    reply, and re-retrieving later would produce today's passages for
    yesterday's words.

    The excerpt is kept too. A citation whose document is later deleted still
    shows what was quoted, which is what makes an old answer auditable rather
    than merely a dead link.
    """

    __tablename__ = "message_citations"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    message_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(f"{CHAT_SCHEMA}.messages.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    document_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    document_version_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    chunk_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    page: Mapped[int | None] = mapped_column(Integer)
    section: Mapped[str | None] = mapped_column(String(500))
    filename: Mapped[str | None] = mapped_column(String(255))
    excerpt: Mapped[str | None] = mapped_column(String(2000))


#: Persisted phases, taken from the wire enum so a stored value and a
#: streamed one cannot drift apart.
TOOL_PHASES = tuple(phase.value for phase in ToolPhase)


class ToolCall(Base):
    """The audit row for one tool call.

    Written when a model asks, not when a person approves, so a denied or
    expired request is recorded too. A tool that was refused is exactly the
    kind of thing someone will later want to know was refused.
    """

    __tablename__ = "tool_calls"
    __table_args__ = (
        CheckConstraint(
            "phase IN (" + ", ".join(f"'{phase}'" for phase in TOOL_PHASES) + ")",
            name="phase_known",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(f"{CHAT_SCHEMA}.conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))

    tool_name: Mapped[str] = mapped_column(String(200), nullable=False)
    server_name: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Sanitized before storage. These are shown back to the user, because
    #: approving "run a command" without seeing the command is not consent.
    arguments: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    phase: Mapped[str] = mapped_column(String(16), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    #: Consent to one action at one moment, so it has an end.
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: The subject who decided, not a user id: an audit row is read by people
    #: and should name the account they recognise.
    decided_by: Mapped[str | None] = mapped_column(String(255))

    #: Bounded. Tool output is untrusted text from a process Primer does not
    #: control, and an unbounded column is a way to fill the disk.
    output: Mapped[str | None] = mapped_column(String(8000))
    error_code: Mapped[str | None] = mapped_column(String(64))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
