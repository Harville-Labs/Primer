"""Running an operator's ComfyUI workflow templates.

ComfyUI is not an images API. It runs a graph of nodes submitted to
`POST /prompt`, reports progress over a WebSocket, lists what each node
produced in `/history/{id}`, and serves the files from `/view`. Inputs are
uploaded first, to `/upload/image`, and referred to by the name ComfyUI
gives back.

What is submitted is always one of the operator's templates with the bound
inputs filled. See `ComfyTemplate` for why a user's workflow never is.

Through `websockets` and HTTP rather than a client library: there is no
maintained official one, and the protocol used here is five endpoints.
"""

from __future__ import annotations

import asyncio
import base64
import copy
import json
import secrets
import uuid
from collections.abc import Awaitable, Callable, Mapping
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx2
from fastapi import status
from primer_contracts.errors import ErrorCode
from primer_contracts.images import ComfyTemplate, ImageRequest, ImageResult
from primer_service.errors import ProblemError
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import WebSocketException

from primer_chat.images import (
    MAX_OUTPUT_BYTES,
    check_declared,
    refused,
    timed_out,
    undeclared,
    unreachable,
    unusable,
)
from primer_chat.providers_store import ResolvedProvider

#: Steps done and steps in all, for the node running now.
Progress = Callable[[int, int], Awaitable[None]]


def template_for(provider: ResolvedProvider, request: ImageRequest) -> ComfyTemplate:
    """The template asked for, if it takes everything the request sets.

    A value with nowhere to go is refused rather than dropped. The OpenAI
    adapter cannot know whether a server reads an extension field; here it
    is known, and a seed silently ignored is a result someone will try to
    reproduce.
    """
    declared = check_declared(provider, request)
    template = declared.template(request.model)
    if template is None:
        # `ImageCapabilities` keeps models and template names the same, so
        # this is a declaration saved before that rule existed.
        raise undeclared(provider, f"run a template called {request.model!r}")
    if request.operation not in template.operations:
        raise undeclared(provider, f"{request.operation} with the template {template.name!r}")
    for field in ("negative_prompt", "seed", "steps"):
        if getattr(request, field) is not None and getattr(template.bindings, field) is None:
            label = field.replace("_", " ")
            raise undeclared(provider, f"take a {label} with the template {template.name!r}")
    return template


def filled(
    template: ComfyTemplate, request: ImageRequest, uploaded: Mapping[str, str], seed: int | None
) -> dict[str, Any]:
    """The template's workflow, with the request's values in its bound inputs.

    Values are assigned into a copy, never substituted into the template's
    text: a prompt containing JSON is still one string in one input, and
    cannot add a node or reach an input nobody bound.
    """
    workflow = copy.deepcopy(template.workflow)
    width, height = (int(side) for side in request.size.split("x"))
    values: dict[str, Any] = {
        "prompt": request.prompt,
        "negative_prompt": request.negative_prompt,
        "seed": seed,
        "width": width,
        "height": height,
        "steps": request.steps,
        "image": uploaded.get("image"),
        "mask": uploaded.get("mask"),
    }
    for field, value in values.items():
        bound = getattr(template.bindings, field)
        if bound is not None and value is not None:
            workflow[bound.node]["inputs"][bound.input] = value
    return workflow


def seed_for(template: ComfyTemplate, request: ImageRequest) -> int | None:
    """The seed to run with, chosen here when the template takes one.

    A template is exported with whatever seed it last ran with, so leaving
    it would make the same image every time. Choosing one here, rather than
    letting a node randomize it, is what lets the result report it.
    """
    if template.bindings.seed is None:
        return None
    return request.seed if request.seed is not None else secrets.randbelow(2**32)


def socket_url(base: str, client_id: str) -> str:
    parts = urlsplit(base)
    scheme = "wss" if parts.scheme == "https" else "ws"
    return urlunsplit((scheme, parts.netloc, f"{parts.path}/ws", f"clientId={client_id}", ""))


def rejected(provider: ResolvedProvider, template: ComfyTemplate, body: Any) -> ProblemError:
    """`/prompt` refused the workflow before running any of it.

    Named by node, and only by nodes the template has: those are the
    operator's own ids, where ComfyUI's messages are not quoted for the
    reason `refused` gives.
    """
    errors = body.get("node_errors") if isinstance(body, Mapping) else None
    nodes = sorted(node for node in errors or {} if node in template.workflow)
    where = f" at node {', '.join(nodes)}" if nodes else ""
    return ProblemError(
        code=ErrorCode.DEPENDENCY_UNAVAILABLE,
        title="Image provider refused the template",
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=(
            f"{provider.name} refused the template {template.name!r}{where}. Check that the "
            "nodes and model files it uses are installed there. Trying again will not help."
        ),
    )


def failed_run(provider: ResolvedProvider, template: ComfyTemplate, node: Any) -> ProblemError:
    where = f" at node {node}" if isinstance(node, str) and node in template.workflow else ""
    return ProblemError(
        code=ErrorCode.DEPENDENCY_UNAVAILABLE,
        title="Image provider failed",
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=f"{provider.name} failed running {template.name!r}{where}. Its log has the reason.",
    )


def lost(provider: ResolvedProvider) -> ProblemError:
    return ProblemError(
        code=ErrorCode.DEPENDENCY_UNAVAILABLE,
        title="Lost contact with the image provider",
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=(
            f"{provider.name} stopped reporting before the image was finished. "
            "It may have restarted; try again."
        ),
    )


async def follow(
    socket: ClientConnection,
    provider: ResolvedProvider,
    template: ComfyTemplate,
    prompt_id: str,
    progress: Progress | None,
) -> bool:
    """Read the run's events until it finishes. False if the socket closed first."""
    try:
        async for message in socket:
            if isinstance(message, bytes):
                continue  # A preview of the image so far, which nothing here shows.
            try:
                event = json.loads(message)
            except ValueError:
                continue
            data = event.get("data") if isinstance(event, Mapping) else None
            if not isinstance(data, Mapping) or data.get("prompt_id") != prompt_id:
                # The socket carries every client's runs, and queue status.
                continue
            kind = event.get("type")
            if kind == "progress" and progress is not None:
                value, most = data.get("value"), data.get("max")
                if isinstance(value, int) and isinstance(most, int) and most > 0:
                    await progress(value, most)
            elif kind == "execution_error":
                raise failed_run(provider, template, data.get("node_id"))
            elif kind == "execution_success" or (kind == "executing" and data.get("node") is None):
                return True
    except WebSocketException:
        return False
    return False


def json_of(provider: ResolvedProvider, response: httpx2.Response) -> Any:
    if response.is_error:
        raise refused(provider, response.status_code)
    try:
        return response.json()
    except ValueError as error:
        raise unusable(provider, "answered with something other than JSON.") from error


class Run:
    """One request against one ComfyUI, sharing a client and its key."""

    def __init__(self, provider: ResolvedProvider, client: httpx2.AsyncClient) -> None:
        self.provider = provider
        self.client = client
        self.base = provider.base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {provider.api_key or 'none'}"}

    async def upload(self, data: bytes) -> str:
        """Put an input where a LoadImage node can read it, and return its name."""
        response = await self.client.post(
            f"{self.base}/upload/image",
            # Named here, so two people's uploads never overwrite each other.
            files={"image": (f"primer-{uuid.uuid4().hex}.png", data, "image/png")},
            data={"type": "input", "overwrite": "true"},
            headers=self.headers,
        )
        body = json_of(self.provider, response)
        name = body.get("name") if isinstance(body, Mapping) else None
        if not isinstance(name, str) or not name:
            raise unusable(self.provider, "did not say where it put an uploaded image.")
        subfolder = body.get("subfolder") or ""
        return f"{subfolder}/{name}" if subfolder else name

    async def submit(
        self, template: ComfyTemplate, workflow: dict[str, Any], client_id: str
    ) -> str:
        response = await self.client.post(
            f"{self.base}/prompt",
            json={"prompt": workflow, "client_id": client_id},
            headers=self.headers,
        )
        if response.status_code == status.HTTP_400_BAD_REQUEST:
            try:
                body = response.json()
            except ValueError:
                body = None
            raise rejected(self.provider, template, body)
        body = json_of(self.provider, response)
        prompt_id = body.get("prompt_id") if isinstance(body, Mapping) else None
        if not isinstance(prompt_id, str):
            raise unusable(self.provider, "accepted the template without saying which run it was.")
        return prompt_id

    async def outputs(self, template: ComfyTemplate, prompt_id: str, finished: bool) -> list[str]:
        """The output node's images, fetched and encoded."""
        history = json_of(
            self.provider,
            await self.client.get(f"{self.base}/history/{prompt_id}", headers=self.headers),
        )
        entry = history.get(prompt_id) if isinstance(history, Mapping) else None
        if not isinstance(entry, Mapping):
            # ComfyUI writes a run's history when it ends, so a socket that
            # dropped before it did leaves none: the run is unfinished, not
            # failed, and asking again is the right advice.
            if not finished:
                raise lost(self.provider)
            raise unusable(self.provider, "reported the run finished but kept no record of it.")
        produced = (entry.get("outputs") or {}).get(template.bindings.output) or {}
        listed = produced.get("images") if isinstance(produced, Mapping) else None
        if not isinstance(listed, list) or not listed:
            raise unusable(self.provider, f"made no images at node {template.bindings.output}.")
        images: list[str] = []
        for item in listed:
            if not isinstance(item, Mapping) or not isinstance(item.get("filename"), str):
                raise unusable(self.provider, "listed an output that is not an image.")
            # Fetched from the provider's own URL by name, never from a
            # location the backend chose.
            response = await self.client.get(
                f"{self.base}/view",
                params={
                    "filename": item["filename"],
                    "subfolder": str(item.get("subfolder") or ""),
                    "type": str(item.get("type") or "output"),
                },
                headers=self.headers,
            )
            if response.is_error:
                raise refused(self.provider, response.status_code)
            if len(response.content) > MAX_OUTPUT_BYTES:
                raise unusable(
                    self.provider, f"made an image larger than {MAX_OUTPUT_BYTES} bytes."
                )
            images.append(base64.b64encode(response.content).decode())
        return images


async def make(
    provider: ResolvedProvider,
    request: ImageRequest,
    *,
    timeout_seconds: float,
    progress: Progress | None = None,
) -> ImageResult:
    """Run the template the request names, reporting progress as it goes."""
    template = template_for(provider, request)
    seed = seed_for(template, request)
    client_id = uuid.uuid4().hex
    try:
        async with (
            asyncio.timeout(timeout_seconds),
            httpx2.AsyncClient(timeout=timeout_seconds) as client,
            # Opened before submitting, or the first events could be sent
            # before anyone was listening for them.
            connect(
                socket_url(provider.base_url.rstrip("/"), client_id),
                additional_headers={"Authorization": f"Bearer {provider.api_key or 'none'}"},
                max_size=MAX_OUTPUT_BYTES,
            ) as socket,
        ):
            run = Run(provider, client)
            uploaded = {
                name: await run.upload(data)
                for name, data in (("image", request.image), ("mask", request.mask))
                if data is not None
            }
            workflow = filled(template, request, uploaded, seed)
            prompt_id = await run.submit(template, workflow, client_id)
            finished = await follow(socket, provider, template, prompt_id, progress)
            images = await run.outputs(template, prompt_id, finished)
    except (TimeoutError, httpx2.TimeoutException) as error:
        raise timed_out(provider) from error
    except (httpx2.RequestError, WebSocketException, OSError) as error:
        raise unreachable(provider) from error
    return ImageResult.model_validate(
        {"provider_id": provider.id, "model": request.model, "images": images, "seed": seed}
    )
