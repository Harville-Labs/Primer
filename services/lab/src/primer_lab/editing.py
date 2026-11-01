"""Editing how the resident model runs: generating with interventions, and steering vectors."""

from __future__ import annotations

import asyncio
import functools
import itertools
import logging
from collections.abc import AsyncIterator
from typing import Any

import torch
from fastapi import APIRouter, Request, status
from fastapi.responses import StreamingResponse
from primer_contracts.errors import ErrorCode
from primer_contracts.lab import (
    GeneratedToken,
    GenerateRequest,
    GenerationCompleted,
    GenerationFailed,
    GenerationQueued,
    GenerationStreamEvent,
    InspectedToken,
    PromptInput,
    SteeringVector,
    SteeringVectorRequest,
    TargetChange,
)
from primer_service.errors import ProblemError, problem_of
from primer_service.sse import encode

from primer_lab.config import Settings
from primer_lab.identity import CurrentPrincipal
from primer_lab.inspection import describe, special_ids
from primer_lab.interventions import Comparison, InterventionRefused, compare, first_divergence
from primer_lab.interventions import resolve as resolve_edits
from primer_lab.routes import check_new_tokens, current_resident, read_prompt, refused, run_queued
from primer_lab.steering import difference_of_means, last_token_residuals

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/lab", tags=["lab"])


class _EventStream(StreamingResponse):
    media_type = "text/event-stream"


def _target_id(tokenizer: Any, target: str) -> int:
    ids = tokenizer.encode(target, add_special_tokens=False)
    if len(ids) != 1:
        raise refused(
            "The target is not one token",
            f"{target!r} is {len(ids)} tokens in this model's vocabulary. Watch one token: "
            "often the word with its leading space.",
        )
    return ids[0]


def _target_change(tokenizer: Any, token: int, comparison: Comparison) -> TargetChange:
    before, after = comparison.baseline.first_logits, comparison.edited.first_logits
    return TargetChange(
        token=InspectedToken(id=token, text=tokenizer.decode([token]), special=False),
        baseline_logit=float(before[token]),
        edited_logit=float(after[token]),
        baseline_probability=float(before.softmax(dim=-1)[token]),
        edited_probability=float(after.softmax(dim=-1)[token]),
    )


@router.post(
    "/generate",
    summary="Generate with and without edits to the forward pass",
    response_class=_EventStream,
    responses={200: {"model": GenerationStreamEvent}},
    description=(
        "Generates greedily from the prompt twice, first as the model is and then with the "
        "interventions applied at every step, and streams both answers token by token as "
        "server-sent events. A request that cannot run is refused before the stream opens; "
        "one that fails once it has started ends the stream with an `error` event."
    ),
)
async def generate(
    request: Request, body: GenerateRequest, principal: CurrentPrincipal
) -> StreamingResponse:
    settings: Settings = request.app.state.settings
    resident = current_resident(request)
    prompt = read_prompt(request, resident, body)
    model = resident.loaded.model
    check_new_tokens(settings, body.max_new_tokens)
    if len(body.interventions) > settings.lab_max_interventions:
        raise refused(
            "Too many edits",
            f"This Lab applies at most {settings.lab_max_interventions} at once "
            "(PRIMER_LAB_MAX_INTERVENTIONS).",
        )
    try:
        edits = resolve_edits(
            body.interventions,
            num_layers=resident.loaded.num_layers,
            hidden_size=model.config.hidden_size,
            mlp_width=model.config.intermediate_size,
            saes=resident.ready_saes,
            vectors=resident.vectors.owned_by(principal.user_id),
        )
    except InterventionRefused as error:
        raise refused("That edit cannot be made", str(error)) from None
    target = _target_id(model.tokenizer, body.target) if body.target else None
    special = special_ids(model.tokenizer)

    loop = asyncio.get_running_loop()
    outbox: asyncio.Queue[Any] = asyncio.Queue()
    ids = itertools.count()
    # Set when the client goes. The run checks it between steps, because
    # the thread cannot be stopped mid-pass and should not be left to
    # finish an answer nobody will read while others wait behind it.
    gone = asyncio.Event()

    def put(kind: type[Any], **fields: Any) -> None:
        # Ids are taken on the loop, in the order events are queued, so
        # they stay monotonic whichever thread produced the event.
        outbox.put_nowait(kind(id=next(ids), **fields))

    def on_token(run: str, index: int, token: int) -> None:
        (described,) = describe(model.tokenizer, [token], special)
        loop.call_soon_threadsafe(
            functools.partial(put, GeneratedToken, run=run, index=index, token=described)
        )

    async def queued(ahead: int) -> None:
        if ahead:
            put(GenerationQueued, ahead=ahead)

    def work() -> Comparison:
        return compare(
            model,
            prompt.ids,
            edits,
            max_new_tokens=body.max_new_tokens,
            special=special,
            cancelled=gone.is_set,
            on_token=on_token,
        )

    async def produce() -> None:
        try:
            comparison = await run_queued(request, resident, work, on_position=queued)
        except ProblemError as error:
            put(GenerationFailed, problem=problem_of(request, error))
            return
        except Exception:
            logger.exception("a generation failed")
            failure = ProblemError(
                code=ErrorCode.INTERNAL_ERROR,
                title="The generation failed",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Lab could not finish this run. The service log has the cause.",
            )
            put(GenerationFailed, problem=problem_of(request, failure))
            return
        baseline, edited = comparison.baseline.tokens, comparison.edited.tokens
        put(
            GenerationCompleted,
            model=resident.status(),
            prompt=describe(model.tokenizer, prompt.ids, special),
            baseline=describe(model.tokenizer, baseline, special),
            edited=describe(model.tokenizer, edited, special),
            diverged_at=first_divergence(baseline, edited),
            target=_target_change(model.tokenizer, target, comparison) if target else None,
        )

    async def events() -> AsyncIterator[str]:
        producer = asyncio.create_task(produce())
        try:
            while True:
                event = await outbox.get()
                yield encode(event)
                if isinstance(event, GenerationCompleted | GenerationFailed):
                    return
        finally:
            gone.set()
            producer.cancel()

    return _EventStream(
        events(),
        # As for Chat: a buffering proxy would hold every token until the end.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post(
    "/vectors",
    summary="Make a steering vector from two sets of prompts",
    status_code=status.HTTP_201_CREATED,
    description=(
        "The mean residual stream after the layer, at each prompt's last token, over the "
        "positive prompts minus the same over the negative ones. Lab holds it for the person "
        "who made it until the model changes; add it with an `add` edit at a residual site."
    ),
)
async def make_vector(
    request: Request, body: SteeringVectorRequest, principal: CurrentPrincipal
) -> SteeringVector:
    resident = current_resident(request)
    if body.layer >= resident.loaded.num_layers:
        raise refused(
            "No such layer",
            f"This model's layers are 0 to {resident.loaded.num_layers - 1}.",
        )

    def ids(prompts: list[str]) -> list[list[int]]:
        return [
            read_prompt(request, resident, PromptInput(prompt=text, chat=body.chat)).ids
            for text in prompts
        ]

    positive, negative = ids(body.positive), ids(body.negative)
    model = resident.loaded.model

    def work() -> torch.Tensor:
        return difference_of_means(
            last_token_residuals(model, positive, body.layer),
            last_token_residuals(model, negative, body.layer),
        )

    vector = await run_queued(request, resident, work)
    return resident.vectors.add(
        principal.user_id,
        vector,
        label=body.label,
        layer=body.layer,
        positive=len(positive),
        negative=len(negative),
    )


@router.get("/vectors", summary="The steering vectors you have made on this model")
async def list_vectors(request: Request, principal: CurrentPrincipal) -> list[SteeringVector]:
    return current_resident(request).vectors.listed(principal.user_id)
