"""Server-sent event framing.

One event per message, typed by its `type` field, with the monotonic id in
the SSE `id:` field so a reconnecting client can resume from what it saw.
Chat streams answers and Lab streams generations in this one shape, so the
web app reads both with the same parser.
"""

from __future__ import annotations

from typing import Protocol


class Event(Protocol):
    @property
    def id(self) -> int: ...

    def model_dump_json(self) -> str: ...


def encode(event: Event) -> str:
    """Frame one event.

    The payload is compact JSON on a single `data:` line. A newline inside
    the body would be read by the client as a field separator, and JSON
    encoding is what guarantees there is not one.
    """
    kind = getattr(event, "type", "message")
    body = event.model_dump_json()
    return f"id: {event.id}\nevent: {kind}\ndata: {body}\n\n"


def comment(text: str) -> str:
    """An SSE comment, which keeps a connection alive without being an event."""
    return f": {text}\n\n"
