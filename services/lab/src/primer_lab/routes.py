"""Lab's status, choosing its model, and inspecting a prompt.

Also the helpers every run shares - finding the resident model, reading a
prompt, and queueing the work - which the editing routes call too.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Request, status
from primer_contracts.errors import ErrorCode
from primer_contracts.lab import (
    ActivationRequest,
    ActivationVector,
    InspectRequest,
    InspectResponse,
    LabStatus,
    LoadModelRequest,
    PromptInput,
)
from primer_service.errors import ProblemError

from primer_lab.config import Settings
from primer_lab.host import ModelHost, Resident, SwitchInProgress
from primer_lab.identity import CurrentPrincipal, can_choose_model
from primer_lab.inspection import (
    Prompt,
    inspect_prompt,
    read_activation,
    tokenize,
)
from primer_lab.queue import PositionCallback, QueueFull, RunQueue, RunTimedOut
from primer_lab.sources import ModelUnavailable

router = APIRouter(prefix="/api/v1/lab", tags=["lab"])


def _status(request: Request, allowed: bool) -> LabStatus:
    settings: Settings = request.app.state.settings
    host: ModelHost = request.app.state.host
    queue: RunQueue = request.app.state.queue
    return LabStatus(
        ready=host.resident is not None,
        model=host.resident.status() if host.resident else None,
        switch=host.switch,
        can_choose_model=allowed,
        max_model_bytes=settings.lab_max_model_bytes,
        queue_depth=queue.depth,
        max_prompt_tokens=settings.lab_max_prompt_tokens,
        max_new_tokens=settings.lab_max_new_tokens,
    )


@router.get("/status", summary="The resident model and any switch in progress")
async def lab_status(request: Request, principal: CurrentPrincipal) -> LabStatus:
    return _status(request, can_choose_model(request.app.state.settings, principal))


@router.put(
    "/model",
    summary="Switch the resident model",
    status_code=status.HTTP_202_ACCEPTED,
    description=(
        "Starts fetching and loading the named model, and answers once that has started. "
        "The switch replaces the model for every user of this Lab; poll the status to see "
        "it finish. Naming the model already resident, or already being switched to, "
        "changes nothing."
    ),
)
async def choose_model(
    request: Request, body: LoadModelRequest, principal: CurrentPrincipal
) -> LabStatus:
    settings: Settings = request.app.state.settings
    if not can_choose_model(settings, principal):
        raise ProblemError(
            code=ErrorCode.IDENTITY_INVALID,
            title="Not allowed to change the model",
            status_code=status.HTTP_403_FORBIDDEN,
            detail="On this deployment the model Lab loads is chosen by "
            + ("administrators." if settings.lab_model_choice == "admins" else "the operator."),
        )
    host: ModelHost = request.app.state.host
    try:
        host.request(body.model)
    except SwitchInProgress:
        raise ProblemError(
            code=ErrorCode.CONFLICT,
            title="Another model is being loaded",
            status_code=status.HTTP_409_CONFLICT,
            detail="Wait for the switch in progress to finish, then try again.",
        ) from None
    except ModelUnavailable as error:
        raise ProblemError(
            code=ErrorCode.VALIDATION_FAILED,
            title="That model cannot be used",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from None
    return _status(request, True)


class ModelChanged(RuntimeError):
    """The resident model was replaced while a run waited for its turn."""


def current_resident(request: Request) -> Resident:
    host: ModelHost = request.app.state.host
    if host.resident is None:
        raise ProblemError(
            code=ErrorCode.CONFLICT,
            title="No model is loaded",
            status_code=status.HTTP_409_CONFLICT,
            detail="Lab is loading a model, or the last load failed. Try again once its "
            "status says it is ready, or choose another model.",
        )
    return host.resident


def read_prompt(request: Request, resident: Resident, body: PromptInput) -> Prompt:
    """Tokenize before queueing, so a prompt that is too long never waits to be refused."""
    settings: Settings = request.app.state.settings
    prompt = tokenize(resident.loaded.model.tokenizer, body.prompt, chat=body.chat)
    if len(prompt.ids) > settings.lab_max_prompt_tokens:
        raise refused(
            "The prompt is too long",
            f"It is {len(prompt.ids)} tokens"
            + (" with the chat template" if prompt.chat_template else "")
            + f", and this Lab reads at most {settings.lab_max_prompt_tokens} "
            "(PRIMER_LAB_MAX_PROMPT_TOKENS).",
        )
    return prompt


def check_new_tokens(settings: Settings, wanted: int) -> None:
    if wanted > settings.lab_max_new_tokens:
        raise refused(
            "Too many new tokens",
            f"This Lab generates at most {settings.lab_max_new_tokens} "
            "(PRIMER_LAB_MAX_NEW_TOKENS).",
        )


def refused(title: str, detail: str) -> ProblemError:
    return ProblemError(
        code=ErrorCode.VALIDATION_FAILED,
        title=title,
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=detail,
    )


async def run_queued[T](
    request: Request,
    resident: Resident,
    work: Callable[[], T],
    *,
    on_position: PositionCallback | None = None,
) -> T:
    """Run `work` on the queue against `resident`, with every failure named."""
    settings: Settings = request.app.state.settings
    host: ModelHost = request.app.state.host
    queue: RunQueue = request.app.state.queue

    def guarded() -> T:
        # A switch loads through this same queue, so by the time this run
        # starts the model it was tokenized for may have gone. Running it
        # anyway would read token ids through a different vocabulary.
        if host.resident is not resident:
            raise ModelChanged
        return work()

    try:
        return await queue.run(
            guarded, limit_seconds=settings.lab_run_timeout_seconds, on_position=on_position
        )
    except ModelChanged:
        raise ProblemError(
            code=ErrorCode.CONFLICT,
            title="The model changed",
            status_code=status.HTTP_409_CONFLICT,
            detail="Someone switched Lab to another model while this run waited. Run it again "
            "to read it on the new one.",
        ) from None
    except QueueFull as error:
        raise ProblemError(
            code=ErrorCode.QUOTA_EXCEEDED,
            title="Lab is busy",
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"{error.waiting} runs are already waiting. Try again shortly.",
        ) from None
    except RunTimedOut as error:
        raise ProblemError(
            code=ErrorCode.QUOTA_EXCEEDED,
            title="The run took too long",
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"It ran past {error.seconds:.0f}s (PRIMER_LAB_RUN_TIMEOUT_SECONDS) and was "
            "abandoned. A shorter prompt runs faster; trying again will not.",
        ) from None


@router.post(
    "/inspect",
    summary="Read the logit lens, top MLP neurons and SAE features for a prompt",
    description=(
        "Runs the prompt through the resident model once and returns, for every layer and "
        "position, the residual stream's norm, the logit lens's likeliest tokens, and the "
        "MLP neurons with the largest activations, and the strongest SAE features at layers "
        "this deployment has SAEs for. Summaries, not raw activations: read "
        "one whole vector with the activation route."
    ),
)
async def inspect(
    request: Request, body: InspectRequest, principal: CurrentPrincipal
) -> InspectResponse:
    resident = current_resident(request)
    prompt = read_prompt(request, resident, body)
    model = resident.loaded.model
    reading = await run_queued(
        request,
        resident,
        lambda: inspect_prompt(
            model, prompt, neurons=body.neurons, features=body.features, saes=resident.ready_saes
        ),
    )
    return InspectResponse(
        model=resident.status(),
        chat_template=prompt.chat_template,
        tokens=reading.tokens,
        vocabulary=reading.vocabulary,
        hidden_size=model.config.hidden_size,
        mlp_width=model.config.intermediate_size,
        layers=reading.layers,
    )


@router.post(
    "/activation",
    summary="Read one whole activation vector",
    description=(
        "The full vector at one site, layer and position of a prompt. A POST rather than a "
        "GET so the prompt travels in the body, not in a URL that proxies and logs keep."
    ),
)
async def activation(
    request: Request, body: ActivationRequest, principal: CurrentPrincipal
) -> ActivationVector:
    resident = current_resident(request)
    prompt = read_prompt(request, resident, body)
    for name, value, bound in (
        ("layer", body.layer, resident.loaded.num_layers),
        ("token", body.token, len(prompt.ids)),
    ):
        if value >= bound:
            raise refused(
                f"No such {name}", f"The {name} must be below {bound} for this model and prompt."
            )
    model = resident.loaded.model
    values = await run_queued(
        request,
        resident,
        lambda: read_activation(model, prompt, layer=body.layer, token=body.token, site=body.site),
    )
    return ActivationVector(
        model=resident.status(),
        layer=body.layer,
        token=body.token,
        site=body.site,
        values=values,
    )
