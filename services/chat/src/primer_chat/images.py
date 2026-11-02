"""Making images through a provider's OpenAI images API.

Chat does this rather than Control because Chat holds the providers and
their keys, as it does for scoring. Control asks for an image and stores
what comes back; it never sees where the request went or with what key.

Every request is checked against what the operator declared before the
backend is called. A server asked for a size or an operation it does not
support often answers anyway, with an image at some other size or an edit
that ignored the mask, and a person reading the result has no way to know.

Through HTTP rather than the OpenAI SDK. The SDK types sizes, models and
operations as the hosted API's own values, and the servers this is for
(vLLM-Omni, LocalAI) take others, and fields the SDK does not know about.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
from typing import Any

import httpx2
from fastapi import status
from primer_contracts.errors import ErrorCode
from primer_contracts.images import ImageCapabilities, ImageRequest, ImageResult
from primer_service.errors import ProblemError

from primer_chat.providers_store import ResolvedProvider

#: A returned image, decoded. Larger than any image the declared sizes
#: allow; what it guards against is a server that streams back something
#: other than an image until Chat runs out of memory.
MAX_OUTPUT_BYTES = 64 * 1024 * 1024


def undeclared(provider: ResolvedProvider, what: str) -> ProblemError:
    """Refused before the backend is called, naming where to declare it."""
    where = (
        "in PRIMER_IMAGES_CAPABILITIES for this deployment"
        if provider.source == "deployment"
        else "for it in settings"
    )
    return ProblemError(
        code=ErrorCode.VALIDATION_FAILED,
        title="Not declared for this provider",
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=(
            f"{provider.name} is not declared to {what}. If its server does, declare it {where}."
        ),
    )


def check_declared(provider: ResolvedProvider, request: ImageRequest) -> ImageCapabilities:
    """What the provider declares, if it covers this request."""
    declared = provider.images
    if declared is None:
        raise undeclared(provider, "make images")
    if request.model not in declared.models:
        raise undeclared(provider, f"serve the image model {request.model!r}")
    if request.operation not in declared.operations:
        raise undeclared(provider, request.operation)
    if request.size not in declared.sizes:
        raise undeclared(provider, f"make {request.size} images")
    if request.count > declared.max_images:
        raise undeclared(provider, f"make {request.count} images at once")
    return declared


def unreachable(provider: ResolvedProvider) -> ProblemError:
    return ProblemError(
        code=ErrorCode.DEPENDENCY_UNAVAILABLE,
        title="Image provider could not be reached",
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=f"{provider.name} could not be reached. It may be starting; try again.",
    )


def timed_out(provider: ResolvedProvider) -> ProblemError:
    return ProblemError(
        code=ErrorCode.DEPENDENCY_UNAVAILABLE,
        title="Image provider took too long",
        status_code=status.HTTP_504_GATEWAY_TIMEOUT,
        detail=(
            f"{provider.name} did not finish in time. It may be busy; try again, "
            "or ask for fewer or smaller images."
        ),
    )


KEY_REFUSED = "Its API key was refused. Correct the key; trying again will not help."

#: What an operator can do about each status, since the body is not quoted.
REFUSALS = {
    status.HTTP_400_BAD_REQUEST: (
        "Check that what is declared for it matches what its server accepts. "
        "Trying again will not help."
    ),
    status.HTTP_401_UNAUTHORIZED: KEY_REFUSED,
    status.HTTP_403_FORBIDDEN: KEY_REFUSED,
    status.HTTP_404_NOT_FOUND: (
        "The model or the images API is not served there. Check the URL and the declared models."
    ),
}


def refused(provider: ResolvedProvider, code: int) -> ProblemError:
    """The backend answered with an error status.

    Its body is not quoted, for the reason `failures` gives: it is written
    by whatever server answered and may echo the credential it rejected.
    """
    if code == status.HTTP_429_TOO_MANY_REQUESTS:
        return ProblemError(
            code=ErrorCode.DEPENDENCY_UNAVAILABLE,
            title="Image provider is busy",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"{provider.name} is rate limiting requests. Try again shortly.",
        )
    advice = REFUSALS.get(code, "Its server failed; trying again may help.")
    return ProblemError(
        code=ErrorCode.DEPENDENCY_UNAVAILABLE,
        title="Image provider refused the request",
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=f"{provider.name} answered {code}. {advice}",
    )


def unusable(provider: ResolvedProvider, why: str) -> ProblemError:
    return ProblemError(
        code=ErrorCode.DEPENDENCY_UNAVAILABLE,
        title="Image provider returned no usable image",
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=f"{provider.name} {why}",
    )


def generation_fields(request: ImageRequest, declared: ImageCapabilities) -> dict[str, Any]:
    """The form both endpoints take, with the extension fields only when set.

    Unset fields are left out rather than sent as null: a server that does
    not know `seed` ignores it, but one that does may refuse a null.
    """
    fields: dict[str, Any] = {
        "model": request.model,
        "prompt": request.prompt,
        "n": request.count,
        "size": request.size,
    }
    optional = {
        "negative_prompt": request.negative_prompt,
        "seed": request.seed,
        "steps": request.steps,
    }
    fields.update({name: value for name, value in optional.items() if value is not None})
    if declared.ask_for_base64:
        fields["response_format"] = "b64_json"
    return fields


def returned_images(provider: ResolvedProvider, body: Mapping[str, Any]) -> list[str]:
    """The base64 images in a response, each checked to decode and bounded."""
    data = body.get("data") if isinstance(body, Mapping) else None
    if not isinstance(data, list) or not data:
        raise unusable(provider, "answered without any images.")
    images: list[str] = []
    for item in data:
        encoded = item.get("b64_json") if isinstance(item, Mapping) else None
        if not isinstance(encoded, str):
            if isinstance(item, Mapping) and item.get("url"):
                # Never fetched: the link is chosen by the backend, and
                # following it would let any backend point Chat anywhere.
                raise unusable(
                    provider,
                    "returned links instead of images. Declare ask_for_base64 for it.",
                )
            raise unusable(provider, "returned an entry that is not an image.")
        try:
            decoded = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as error:
            raise unusable(provider, "returned an image that is not valid base64.") from error
        if len(decoded) > MAX_OUTPUT_BYTES:
            raise unusable(provider, f"returned an image larger than {MAX_OUTPUT_BYTES} bytes.")
        images.append(encoded)
    return images


async def make(
    provider: ResolvedProvider, request: ImageRequest, *, timeout_seconds: float
) -> ImageResult:
    """Generate or edit, as the request asks, through `provider`."""
    declared = check_declared(provider, request)
    fields = generation_fields(request, declared)
    base = provider.base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {provider.api_key or 'none'}"}
    try:
        async with httpx2.AsyncClient(timeout=timeout_seconds) as client:
            if request.operation == "generate":
                response = await client.post(
                    f"{base}/images/generations", json=fields, headers=headers
                )
            else:
                files = {"image": ("image.png", request.image or b"", "image/png")}
                if request.mask is not None:
                    files["mask"] = ("mask.png", request.mask, "image/png")
                response = await client.post(
                    f"{base}/images/edits",
                    data={name: str(value) for name, value in fields.items()},
                    files=files,
                    headers=headers,
                )
    except httpx2.TimeoutException as error:
        raise timed_out(provider) from error
    except httpx2.RequestError as error:
        raise unreachable(provider) from error
    if response.is_error:
        raise refused(provider, response.status_code)
    try:
        body = response.json()
    except ValueError as error:
        raise unusable(provider, "answered with something other than JSON.") from error
    return ImageResult.model_validate(
        {
            "provider_id": provider.id,
            "model": request.model,
            # A server that returns more than asked for is held to the count,
            # which is also what the quota upstream was charged for.
            "images": returned_images(provider, body)[: request.count],
        }
    )
