"""Asking Chat for images, as the person who asked Control.

Control holds no provider configuration: Chat resolves a provider id to a
URL and a key, and Chat alone calls the backend. Control calls Chat with
the identity headers the proxy set on the request it is answering, so Chat
applies that person's provider visibility rather than Control's.

Every way this can fail ends as an `ImageFailed`, not an exception. The
caller is a detached task that has already opened a stream to the browser,
and an event is the only thing it can still send.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated

import httpx2
from fastapi import status
from primer_contracts.errors import ErrorCode
from primer_contracts.identity import Principal
from primer_contracts.images import ImageDone, ImageFailed, ImageProgress, ImageRequest
from pydantic import Field, TypeAdapter, ValidationError

from primer_control.config import Settings

ChatEvent = Annotated[ImageProgress | ImageDone | ImageFailed, Field(discriminator="type")]
events = TypeAdapter(ChatEvent)


def identity_headers(principal: Principal, settings: Settings) -> dict[str, str]:
    """The headers Chat reads a principal from, under the names it reads.

    Chat ignores them with authentication off, as Control does, so they are
    sent either way rather than branching on a mode both services share.
    """
    headers = {settings.subject_header: principal.subject}
    if principal.email:
        headers[settings.email_header] = principal.email
    if principal.groups:
        headers[settings.groups_header] = settings.groups_delimiter.join(principal.groups)
    return headers


def failure(
    title: str, detail: str, *, status_code: int, code: ErrorCode = ErrorCode.DEPENDENCY_UNAVAILABLE
) -> ImageFailed:
    return ImageFailed(id=0, code=code, title=title, detail=detail, status=status_code)


def unreachable() -> ImageFailed:
    return failure(
        "Chat is unreachable",
        "Primer's Chat service did not answer, so no image was made. Try again shortly.",
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
    )


def timed_out(seconds: float) -> ImageFailed:
    return failure(
        "Image generation timed out",
        f"No image arrived within {seconds:g} seconds. The provider may be busy; try again.",
        status_code=status.HTTP_504_GATEWAY_TIMEOUT,
    )


def refused(response: httpx2.Response, body: bytes) -> ImageFailed:
    """Chat's own refusal, passed on, since its words are already Primer's.

    Chat answers in the problem shape before a stream opens: an undeclared
    operation, a provider the person cannot see. Anything that is not that
    shape came from something between the two services.
    """
    try:
        problem = json.loads(body)
    except ValueError:
        problem = None
    if isinstance(problem, dict) and isinstance(problem.get("title"), str):
        try:
            return ImageFailed(
                id=0,
                code=str(problem.get("code") or ErrorCode.DEPENDENCY_UNAVAILABLE),
                title=problem["title"],
                detail=problem.get("detail") if isinstance(problem.get("detail"), str) else None,
                status=response.status_code if response.status_code >= 400 else 502,
            )
        except ValidationError:
            pass
    return failure(
        "Chat refused the request",
        f"Chat answered {response.status_code} without saying why.",
        status_code=status.HTTP_502_BAD_GATEWAY,
    )


async def stream_images(
    request: ImageRequest, principal: Principal, settings: Settings
) -> AsyncIterator[ImageProgress | ImageDone | ImageFailed]:
    """Chat's events for one request, ending with a done or a failed."""
    if not settings.chat_service_url:
        yield unreachable()
        return
    url = f"{settings.chat_service_url.rstrip('/')}/api/v1/images/stream"
    timeout = settings.studio_timeout_seconds
    try:
        # A deadline for the whole run as well as the client's per-read one:
        # a provider that reports progress forever would never trip the
        # second.
        async with (
            asyncio.timeout(timeout),
            httpx2.AsyncClient(timeout=httpx2.Timeout(timeout, connect=10.0)) as client,
            client.stream(
                "POST",
                url,
                content=request.model_dump_json(),
                headers={
                    "Content-Type": "application/json",
                    **identity_headers(principal, settings),
                },
            ) as response,
        ):
            if response.status_code != status.HTTP_200_OK:
                yield refused(response, await response.aread())
                return
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                try:
                    event = events.validate_json(line.removeprefix("data: "))
                except ValidationError:
                    yield failure(
                        "Chat sent something unexpected",
                        "Chat's image stream could not be read. This is a bug in Primer.",
                        status_code=status.HTTP_502_BAD_GATEWAY,
                        code=ErrorCode.INTERNAL_ERROR,
                    )
                    return
                yield event
                if not isinstance(event, ImageProgress):
                    return
    except (TimeoutError, httpx2.TimeoutException):
        yield timed_out(timeout)
        return
    except httpx2.RequestError:
        yield unreachable()
        return
    # The stream ended without a result: Chat restarted mid-run.
    yield unreachable()
