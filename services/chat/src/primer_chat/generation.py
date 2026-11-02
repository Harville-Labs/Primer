"""The model boundary.

Chat talks to any OpenAI-compatible endpoint - vLLM, Ollama, llama.cpp, or a
hosted API. The generator is a protocol so the orchestration can be tested
without one, and so swapping Haystack's client later touches one class.
"""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator, Iterator, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import anyio
import anyio.from_thread
import anyio.to_thread
from haystack.components.generators.chat import OpenAIChatGenerator
from haystack.dataclasses import ChatMessage, ToolCall
from haystack.tools import Tool
from haystack.utils import Secret
from primer_contracts.chat import MessageRole

from primer_chat.config import Settings
from primer_chat.rag import HistoryTurn
from primer_chat.reasoning import Channel, Fragment, ReasoningSplitter

#: Where an endpoint that separates thinking itself puts it. There is no
#: standard for this - it is not in the OpenAI API - so the field is whatever
#: a given server chose. These are the two in circulation: `reasoning_content`
#: from vLLM's reasoning parsers and DeepSeek, `reasoning` from others.
REASONING_FIELDS = ("reasoning_content", "reasoning")

#: Where `ReasoningCarried` puts what it rescued, for `_reasoning_of` to
#: find. Under Primer's own name so it cannot collide with a key Haystack
#: adds later.
CARRIED_REASONING = "primer_reasoning"


def _delta_reasoning(chunk: object) -> str:
    """Separated thinking on a raw OpenAI streaming chunk, if there is any.

    Read from the wire object rather than from Haystack's, because this is
    the only place it still exists. The OpenAI client keeps fields it does
    not model as attributes on the delta, which is how a non-standard one
    survives parsing at all.
    """
    choices = getattr(chunk, "choices", None) or ()
    for choice in choices:
        delta = getattr(choice, "delta", None)
        if delta is None:
            continue
        for field in REASONING_FIELDS:
            value = getattr(delta, field, None)
            if isinstance(value, str) and value:
                return value
    return ""


def _reasoning_of(chunk: object) -> str:
    """Thinking a provider handed over already separated, if it did.

    Haystack keeps fields it does not model itself in `meta`, so both are
    looked at: the attribute for a client that grew one, and the bag for
    everything else.
    """
    meta = getattr(chunk, "meta", None) or {}
    if isinstance(meta, dict):
        carried = meta.get(CARRIED_REASONING)
        if isinstance(carried, str) and carried:
            return carried
    for field in REASONING_FIELDS:
        value = getattr(chunk, field, None) or (meta.get(field) if isinstance(meta, dict) else None)
        if isinstance(value, str) and value:
            return value
    return ""


class ReasoningCarried(OpenAIChatGenerator):
    """An OpenAI generator that does not throw separated thinking away.

    Haystack builds a `StreamingChunk`'s `meta` from a fixed set of keys,
    and `reasoning_content` is not one of them - so an endpoint that hands
    thinking over already separated had it discarded between the wire and
    the callback. Primer then showed no thinking at all for precisely the
    models doing the most of it, and the harder a deployment tried to
    configure reasoning properly the less it saw: turning a server's
    reasoning parser on moves the thinking out of the content and into the
    field that was being dropped.

    The rescue happens here because this is the last point where the raw
    chunk and the converted one are both reachable. The stream is wrapped
    rather than the loop reimplemented: Haystack pulls a raw chunk,
    converts it, and calls back, strictly in that order and one for one, so
    remembering what the raw chunk carried is enough for the callback that
    immediately follows it. Reimplementing the loop would mean keeping a
    copy of Haystack's chunk assembly in step with theirs forever.
    """

    def _handle_stream_response(self, chat_completion: Any, callback: Any) -> Any:
        remembered = {"reasoning": ""}

        def raw() -> Iterator[Any]:
            for chunk in chat_completion:
                remembered["reasoning"] = _delta_reasoning(chunk)
                yield chunk

        def forward(converted: Any) -> Any:
            if remembered["reasoning"]:
                converted.meta[CARRIED_REASONING] = remembered["reasoning"]
            return callback(converted)

        # Annotated `Stream`, but only ever iterated - Haystack says as much
        # where it declines to isinstance-check this for the same reason:
        # observability tools wrap the stream and hand back another type.
        return super()._handle_stream_response(raw(), forward)  # ty: ignore[invalid-argument-type]

    async def _handle_async_stream_response(self, chat_completion: Any, callback: Any) -> Any:
        """The same rescue on the async path.

        Primer answers on the sync one, but a generator that lost reasoning
        again the moment someone switched would be a trap rather than a
        limitation.
        """
        remembered = {"reasoning": ""}

        async def raw() -> AsyncIterator[Any]:
            async for chunk in chat_completion:
                remembered["reasoning"] = _delta_reasoning(chunk)
                yield chunk

        async def forward(converted: Any) -> Any:
            if remembered["reasoning"]:
                converted.meta[CARRIED_REASONING] = remembered["reasoning"]
            result = callback(converted)
            if inspect.isawaitable(result):
                await result

        return await super()._handle_async_stream_response(
            raw(),  # ty: ignore[invalid-argument-type]
            forward,
        )


class NoEndpoint(Exception):
    """There is nowhere to send a question, so none is sent.

    Its own type because the caller turns it into a message a person can act
    on. Falling through to the generic failure would report it as a model
    that stopped mid-answer, which sends whoever reads it looking at the
    wrong thing entirely.
    """


@dataclass(frozen=True)
class Endpoint:
    """Where to send a question, and what to authenticate with.

    Passed per request rather than read from settings, because which endpoint
    answers is now a property of the question - a deployment may hold several
    and the asker chose one.
    """

    base_url: str | None
    api_key: str | None = None


@dataclass(frozen=True)
class ToolExchange:
    """A call the model made this turn, and what came back from it.

    Replayed after the question on the next request, so the model reads the
    result as the answer to its own call rather than as something the user
    said. `failed` marks a result that is an explanation of why nothing ran -
    a refusal, an expiry, a tool that broke - so the model is not invited to
    quote it as though it were what the tool found.
    """

    call: ToolCall
    result: str
    failed: bool = False


class ChatGenerator(Protocol):
    """Yields fragments as the model produces them, thinking and answer apart.

    When it is offered tools, it may also yield the calls the model made.
    They come last, after every fragment of the reply that made them: an
    OpenAI-compatible endpoint only says a call is complete when the reply
    is.
    """

    def stream(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        history: tuple[HistoryTurn, ...] = (),
        model: str | None = None,
        endpoint: Endpoint | None = None,
        tools: Sequence[Tool] = (),
        exchanges: tuple[ToolExchange, ...] = (),
    ) -> AsyncIterator[Fragment | ToolCall]: ...


class HaystackChatGenerator:
    """Streams from an OpenAI-compatible endpoint through Haystack.

    One client per model, made on first use and kept. They are cheap to hold
    and not free to build, and a deployment offering several models will use
    each of them repeatedly.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        #: Keyed by endpoint as well as model. A deployment can hold several
        #: providers serving the same model name, and keying on the name
        #: alone would send a question to whichever of them happened to be
        #: asked first - and keep doing so after an administrator changed
        #: the endpoint, because the cached client would still be there.
        self._generators: dict[tuple[str, str], OpenAIChatGenerator] = {}

    @property
    def model(self) -> str | None:
        return self._settings.chat_model

    def _for(self, model: str | None, endpoint: Endpoint | None = None) -> OpenAIChatGenerator:
        """A client for one model at one endpoint.

        The name is not validated here. Whether a user may ask for a model is
        a question about the request, answered where the request is handled;
        by this point it has been.
        """
        target = endpoint or Endpoint(
            base_url=self._settings.chat_base_url,
            api_key=(
                self._settings.chat_api_key.get_secret_value()
                if self._settings.chat_api_key
                else None
            ),
        )
        name = model or self._settings.chat_model
        if name is None:
            raise NoEndpoint("No model was chosen and this deployment configures no default.")
        # Refused rather than defaulted. The OpenAI client reads a null base
        # URL as its own hosted API, so a deployment that has simply not been
        # pointed anywhere would send its users' questions - and the passages
        # retrieved from their private documents - to a third party. Primer
        # is self-hosted; that failure has to be loud.
        if not target.base_url:
            raise NoEndpoint(
                "No inference endpoint is configured, so there is nowhere to send this question."
            )

        key = (target.base_url, name)
        if key not in self._generators:
            self._generators[key] = ReasoningCarried(
                # Many local servers ignore the key but require the header.
                api_key=Secret.from_token(target.api_key or "none"),
                model=name,
                api_base_url=target.base_url,
                timeout=self._settings.chat_timeout_seconds,
            )
        return self._generators[key]

    async def stream(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        history: tuple[HistoryTurn, ...] = (),
        model: str | None = None,
        endpoint: Endpoint | None = None,
        tools: Sequence[Tool] = (),
        exchanges: tuple[ToolExchange, ...] = (),
    ) -> AsyncIterator[Fragment | ToolCall]:
        """Bridge Haystack's synchronous callback into an async iterator.

        Haystack streams by invoking a callback on a worker thread. The
        fragments are handed across a memory channel so the event loop keeps
        serving other connections while a slow model produces tokens.

        Thinking and answer come out on separate channels. An endpoint
        running a reasoning parser hands them over already separated, in a
        field beside the content; one without a parser puts the thinking in
        the content inside `<think>` tags, which the splitter pulls back
        apart. Both are handled, because which of the two a deployment gets
        is a property of how its endpoint was launched, not of Primer.
        """
        send, receive = anyio.create_memory_object_stream[Fragment | ToolCall | None](
            max_buffer_size=64
        )
        splitter = ReasoningSplitter()

        def on_chunk(chunk: object) -> None:
            # A provider that separates thinking itself needs no parsing, and
            # must not be fed through the splitter: its content field carries
            # no tags, and its reasoning is not the answer.
            thought = _reasoning_of(chunk)
            if thought:
                anyio.from_thread.run(send.send, Fragment(Channel.REASONING, thought))
            text = getattr(chunk, "content", "") or ""
            for fragment in splitter.feed(text):
                anyio.from_thread.run(send.send, fragment)

        async def produce() -> None:
            calls: list[ToolCall] = []
            try:
                calls = await anyio.to_thread.run_sync(
                    lambda: self._run(
                        system_prompt,
                        user_prompt,
                        history,
                        on_chunk,
                        model,
                        endpoint,
                        tools=tools,
                        exchanges=exchanges,
                    )
                )
            finally:
                # Anything the splitter was holding back for a tag that never
                # arrived belongs to the reader, not to the parser.
                for fragment in splitter.finish():
                    await send.send(fragment)
                for call in calls:
                    await send.send(call)
                await send.send(None)

        async with anyio.create_task_group() as group:
            group.start_soon(produce)
            async with receive:
                async for item in receive:
                    if item is None:
                        break
                    yield item

    def _run(
        self,
        system_prompt: str,
        user_prompt: str,
        history: tuple[HistoryTurn, ...],
        on_chunk: object,
        model: str | None,
        endpoint: Endpoint | None = None,
        *,
        tools: Sequence[Tool] = (),
        exchanges: tuple[ToolExchange, ...] = (),
    ) -> list[ToolCall]:
        result = self._for(model, endpoint).run(
            messages=[
                ChatMessage.from_system(system_prompt),
                *_replay(history),
                ChatMessage.from_user(user_prompt),
                *_exchanged(exchanges),
            ],
            streaming_callback=on_chunk,  # ty: ignore[invalid-argument-type]
            # None rather than an empty list: some servers reject a request
            # that carries `tools: []`, and a turn with nothing offered
            # should look exactly like one from before tools existed.
            tools=list(tools) or None,
        )
        replies: list[ChatMessage] = result.get("replies", [])
        return [call for reply in replies for call in reply.tool_calls]


def _replay(history: tuple[HistoryTurn, ...]) -> list[ChatMessage]:
    """Earlier turns, as messages between the system prompt and the question.

    The system prompt is not replayed with them. It describes how to answer
    the question being asked now - which passages are in front of the model,
    and how they may be numbered - and an older copy of those instructions
    would be describing passages that are no longer there.
    """
    return [
        ChatMessage.from_user(turn.content)
        if turn.role is MessageRole.USER
        else ChatMessage.from_assistant(turn.content)
        for turn in history
    ]


def _exchanged(exchanges: tuple[ToolExchange, ...]) -> list[ChatMessage]:
    """This turn's tool calls and their results, in the order they happened.

    One assistant message per call, each followed by its result. A reply
    that made two calls at once is replayed as two, which every
    OpenAI-compatible server accepts and which keeps a call and its result
    adjacent however many there were.
    """
    messages: list[ChatMessage] = []
    for exchange in exchanges:
        messages.append(ChatMessage.from_assistant(tool_calls=[exchange.call]))
        messages.append(
            ChatMessage.from_tool(exchange.result, origin=exchange.call, error=exchange.failed)
        )
    return messages


class StaticGenerator:
    """A generator that replays fixed fragments. For tests and for offline use.

    Plain strings are answer text, which is what almost every caller wants.
    A caller exercising a reasoning model passes `Fragment`s instead and says
    which channel each belongs to.
    """

    def __init__(
        self, fragments: Iterator[str | Fragment] | list[str | Fragment], model: str = "static"
    ) -> None:
        self._fragments = [
            fragment if isinstance(fragment, Fragment) else Fragment(Channel.ANSWER, fragment)
            for fragment in fragments
        ]
        self.model = model

    async def stream(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        history: tuple[HistoryTurn, ...] = (),
        model: str | None = None,
        endpoint: Endpoint | None = None,
        tools: Sequence[Tool] = (),
        exchanges: tuple[ToolExchange, ...] = (),
    ) -> AsyncIterator[Fragment | ToolCall]:
        for fragment in self._fragments:
            yield fragment
