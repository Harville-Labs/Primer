"""Scoring the resident model's surprise at each token, for the Anomalies tab.

Chat scores an operator's endpoint from the top-k logprobs it returns. Here
the whole vocabulary is in hand, so entropy is exact rather than a lower
bound; the arithmetic is `primer_anomaly`'s either way.
"""

from __future__ import annotations

from typing import Any, Literal

import torch
from fastapi import APIRouter, Request
from primer_anomaly import SequenceScore, entropy_bits, score_exact
from primer_contracts.lab import LabModelStatus, PromptInput
from pydantic import BaseModel, ConfigDict, Field

from primer_lab.config import Settings
from primer_lab.identity import CurrentPrincipal
from primer_lab.inspection import special_ids
from primer_lab.interventions import generate
from primer_lab.routes import check_new_tokens, current_resident, read_prompt, refused, run_queued

router = APIRouter(prefix="/api/v1/lab", tags=["lab"])

#: Positions measured at once. Each is a float64 row over the vocabulary -
#: about a megabyte at 150 thousand tokens - so this bounds what scoring
#: holds beyond the logits the forward pass produced anyway.
BLOCK = 64

#: `generate` scores what the model writes in answer to `prompt`; `text`
#: scores `prompt` itself. The same words as Chat's, so the tab sends one
#: request shape whichever source it is scoring.
ScoreMode = Literal["generate", "text"]


class LabScoreRequest(PromptInput):
    mode: ScoreMode
    #: How long an answer to generate. Ignored when scoring the text itself.
    max_new_tokens: int = Field(default=64, ge=1, le=2048)


class LabScored(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model: LabModelStatus
    mode: ScoreMode
    #: The answer that was generated, or the text as supplied.
    text: str
    #: Always exact: `estimate` is `full`, and every token has `entropy_bits`.
    score: SequenceScore


def score_from(model: Any, ids: list[int], start: int, special: set[int]) -> SequenceScore | None:
    """Score `ids[start:]` as the model predicted each from what came before it.

    Special tokens are passed over: a chat template's markers and the end
    of an answer are the model's punctuation rather than its text, and an
    end-of-sequence token is often the least likely thing at its position
    without that meaning anything. None when nothing is left to score.
    """
    targets = [i for i in range(max(start, 1), len(ids)) if ids[i] not in special]
    if not targets:
        return None
    read: list[Any] = []
    with torch.no_grad(), model.trace(torch.tensor([ids])):
        read.append(model.output.logits[0])
    logits = read[0]
    logprobs: list[float] = []
    entropies: list[float] = []
    for block in (targets[i : i + BLOCK] for i in range(0, len(targets), BLOCK)):
        # The logits at position i - 1 are the prediction of token i.
        rows = logits[[i - 1 for i in block]].double().log_softmax(dim=-1)
        came = torch.tensor([ids[i] for i in block]).unsqueeze(1)
        logprobs += rows.gather(1, came).squeeze(1).tolist()
        entropies += entropy_bits(rows.cpu().numpy()).tolist()
    tokens = [model.tokenizer.decode([ids[i]]) for i in targets]
    return score_exact(tokens, logprobs, entropies)


@router.post(
    "/score",
    summary="Score the resident model's surprise at each token",
    description=(
        "Either generates a greedy answer to the prompt and scores it, or scores the prompt "
        "itself as plain text, without the chat template. Entropy is over the whole "
        "vocabulary. The first token of a text is not scored: nothing came before it."
    ),
)
async def score(request: Request, body: LabScoreRequest, principal: CurrentPrincipal) -> LabScored:
    settings: Settings = request.app.state.settings
    resident = current_resident(request)
    model = resident.loaded.model
    special = special_ids(model.tokenizer)
    if body.mode == "text":
        # Scored as written. Through the template, the template's own text
        # would be scored as though the user had typed it.
        prompt = read_prompt(request, resident, body.model_copy(update={"chat": False}))
        if not any(i not in special for i in prompt.ids[1:]):
            raise refused(
                "Nothing to score",
                "The text is one token, and the first token of a text has nothing before it "
                "to be predicted from. Score a longer text.",
            )

        def work() -> SequenceScore | None:
            return score_from(model, prompt.ids, 1, special)

        scored = await run_queued(request, resident, work)
        text = body.prompt
    else:
        check_new_tokens(settings, body.max_new_tokens)
        prompt = read_prompt(request, resident, body)
        answer: list[int] = []

        def work() -> SequenceScore | None:
            answer.extend(
                generate(
                    model,
                    prompt.ids,
                    [],
                    max_new_tokens=body.max_new_tokens,
                    special=special,
                    # Not streamed, so there is no client to notice leaving;
                    # the run timeout is what bounds it.
                    cancelled=lambda: False,
                    on_token=lambda index, token: None,
                ).tokens
            )
            return score_from(model, prompt.ids + answer, len(prompt.ids), special)

        scored = await run_queued(request, resident, work)
        text = model.tokenizer.decode(answer, skip_special_tokens=True)
    if scored is None:
        raise refused(
            "Nothing to score",
            "The model ended its answer before writing anything. Try another prompt.",
        )
    return LabScored(model=resident.status(), mode=body.mode, text=text, score=scored)
