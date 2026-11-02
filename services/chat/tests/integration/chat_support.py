"""Test helpers and fakes for the Chat integration suite.

Named for its suite rather than `support`, and holding the fakes rather than
leaving them in conftest: every test directory goes on sys.path, so a shared
name shadows, and `conftest` in particular resolves to whichever one is
found first.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

from httpx2 import AsyncClient, Response
from primer_chat.clients import LibraryForbidden
from primer_chat.config import Settings
from primer_chat.generation import Endpoint
from primer_chat.rag import HistoryTurn
from primer_chat.reasoning import Channel, Fragment
from primer_contracts.identity import Principal
from primer_contracts.indexing import (
    LibraryScope,
    Passage,
    PassagesRequest,
    PassagesResult,
    SearchRequest,
    SearchResult,
)
from primer_contracts.retrieval import RetrievedChunk, SourceLocator

LIBRARY_ID = uuid.uuid5(uuid.NAMESPACE_URL, "chat-library")
OTHER_LIBRARY_ID = uuid.uuid5(uuid.NAMESPACE_URL, "other-library")
GENERATION_ID = uuid.uuid5(uuid.NAMESPACE_URL, "chat-generation")


class FakeControl:
    """Control, recording every authorization question it was asked."""

    def __init__(self, *, allowed: set[UUID] | None = None) -> None:
        self.allowed = allowed if allowed is not None else {LIBRARY_ID}
        self.asked: list[tuple[str, UUID]] = []
        self.generations: tuple[UUID, ...] = (GENERATION_ID,)

    async def library_scope(self, principal: Principal, library_id: UUID) -> LibraryScope:
        self.asked.append((principal.subject, library_id))
        if library_id not in self.allowed:
            raise LibraryForbidden(str(library_id))
        return LibraryScope(library_id=library_id, generation_ids=self.generations)


class FakeRetrieval:
    """Retrieval, recording every search so the tests can prove one never happened."""

    def __init__(self, contents: list[str] | None = None, failure: Exception | None = None) -> None:
        self.contents = contents if contents is not None else ["The conclusion is well supported."]
        self.failure = failure
        self.searches: list[SearchRequest] = []
        self.listings: list[PassagesRequest] = []

    async def search(self, request: SearchRequest) -> SearchResult:
        self.searches.append(request)
        if self.failure is not None:
            raise self.failure
        return SearchResult(
            chunks=tuple(
                RetrievedChunk(
                    chunk_id=uuid.uuid5(uuid.NAMESPACE_URL, f"chunk-{index}-{content}"),
                    library_id=request.library_id,
                    document_id=uuid.uuid5(uuid.NAMESPACE_URL, "document"),
                    document_version_id=uuid.uuid5(uuid.NAMESPACE_URL, "version"),
                    content=content,
                    score=1.0 - index / 100,
                    locator=SourceLocator(page=index + 1, section="Findings"),
                    index_generation=request.generation_ids[0],
                )
                for index, content in enumerate(self.contents)
            )
        )

    async def passages(self, request: PassagesRequest) -> PassagesResult:
        self.listings.append(request)
        if self.failure is not None:
            raise self.failure
        return PassagesResult(
            passages=tuple(
                Passage(
                    chunk_id=uuid.uuid5(uuid.NAMESPACE_URL, f"chunk-{index}-{content}"),
                    document_id=uuid.uuid5(uuid.NAMESPACE_URL, "document"),
                    filename="paper.pdf",
                    ordinal=index,
                    content=content,
                    locator=SourceLocator(page=index + 1, section="Findings"),
                )
                for index, content in enumerate(self.contents[: request.limit])
            ),
            total=len(self.contents),
        )


def deployment(**overrides: object) -> Settings:
    """A plausible deployment, with whatever this test needs changed.

    Chat used to default its model to a hosted one's name, so a test could
    configure nothing and still be answered. Neither the model nor the
    endpoint is defaulted now - a deployment pointed nowhere is refused
    rather than sent to whoever owns the default - so the baseline has to
    say what it is, in one place rather than in every override.

    The generator is faked, so nothing is sent to this address; naming it is
    what makes the deployment under test a configured one.
    """
    # Overrides win, so a test naming its own model is not fighting the
    # baseline for the same keyword.
    return Settings(
        **{
            "auth_mode": "oidc",
            "chat_base_url": "http://model.invalid/v1",
            "chat_model": "test-model",
            **overrides,
        }  # ty: ignore[invalid-argument-type]
    )


class FakeGenerator:
    """A model, recording the prompts it was given."""

    model = "fake-model"

    def __init__(self, fragments: list[str | Fragment] | None = None, fail: bool = False) -> None:
        #: Plain strings are answer text, which is what almost every test
        #: wants. A test about a reasoning model passes `Fragment`s and says
        #: which channel each belongs to.
        raw: list[str | Fragment] = (
            fragments if fragments is not None else ["Grounded ", "answer [1]."]
        )
        self.fragments = [
            piece if isinstance(piece, Fragment) else Fragment(Channel.ANSWER, piece)
            for piece in raw
        ]
        self.fail = fail
        self.prompts: list[tuple[str, str]] = []
        #: The conversation each call was given, so a test can prove the
        #: model was shown the turns before it rather than only the question.
        self.histories: list[tuple[tuple[str, str], ...]] = []
        #: Which model each call asked for, so a test can prove the choice
        #: reached the provider rather than stopping at the request.
        self.models: list[str | None] = []

    @property
    def answer(self) -> str:
        """What the fake will have written as the answer, for comparison.

        Tests assert a stored message against this. Reassembling it at each
        call site would mean every one of them deciding again which channel
        counts, and getting it wrong quietly.
        """
        return "".join(
            fragment.text for fragment in self.fragments if fragment.channel is Channel.ANSWER
        )

    async def stream(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        history: tuple[HistoryTurn, ...] = (),
        model: str | None = None,
        endpoint: Endpoint | None = None,
    ) -> AsyncIterator[Fragment]:
        self.prompts.append((system_prompt, user_prompt))
        self.histories.append(tuple((turn.role.value, turn.content) for turn in history))
        self.models.append(model)
        for index, fragment in enumerate(self.fragments):
            if self.fail and index == 1:
                raise RuntimeError("the endpoint went away")
            yield fragment


def parse_events(body: str) -> list[dict[str, Any]]:
    """Read an SSE body back into the events it framed."""
    events = []
    for block in body.split("\n\n"):
        if not block.strip() or block.startswith(":"):
            continue
        payload = next(
            (line[len("data: ") :] for line in block.splitlines() if line.startswith("data: ")),
            None,
        )
        if payload is not None:
            events.append(json.loads(payload))
    return events


class ChatUser:
    """One edge-authenticated subject talking to Chat."""

    def __init__(self, http: AsyncClient, subject: str) -> None:
        self._http = http
        self._headers = {"X-Forwarded-User": subject}

    async def ask(
        self,
        library_id: str | None,
        message: str,
        *,
        model: str | None = None,
        breadth: str | None = None,
    ) -> list[dict[str, Any]]:
        """Ask a question, of a library or of nothing.

        `None` omits a field rather than sending null, which is how a client
        with nothing selected actually behaves.
        """
        return parse_events((await self.post_ask(message, library_id, model, breadth)).text)

    async def post_ask(
        self,
        message: str,
        library_id: str | None = None,
        model: str | None = None,
        breadth: str | None = None,
    ) -> Response:
        """The raw response, for the cases where it is not a stream."""
        payload: dict[str, Any] = {"message": message}
        if library_id is not None:
            payload["library_id"] = library_id
        if model is not None:
            payload["model"] = model
        if breadth is not None:
            payload["breadth"] = breadth
        return await self._http.post("/api/v1/conversations", json=payload, headers=self._headers)

    async def follow_up(
        self, conversation_id: str, message: str, *, breadth: str | None = None
    ) -> Response:
        payload: dict[str, Any] = {"message": message}
        if breadth is not None:
            payload["breadth"] = breadth
        return await self._http.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json=payload,
            headers=self._headers,
        )

    async def post(self, path: str, payload: dict[str, Any] | None = None) -> Response:
        return await self._http.post(path, json=payload or {}, headers=self._headers)

    async def get(self, path: str) -> Response:
        return await self._http.get(path, headers=self._headers)

    async def patch(self, path: str, payload: dict[str, Any] | None = None) -> Response:
        return await self._http.patch(path, json=payload or {}, headers=self._headers)

    async def delete(self, path: str) -> Response:
        return await self._http.delete(path, headers=self._headers)


class GroupedUser(ChatUser):
    """A subject the proxy also asserts groups for."""

    def __init__(self, http: AsyncClient, subject: str, groups: str = "") -> None:
        super().__init__(http, subject)
        if groups:
            self._headers["X-Forwarded-Groups"] = groups
