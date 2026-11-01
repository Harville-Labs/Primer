"""Scoring text against a provider's model, for the Anomalies tab.

Any signed-in user may, on any enabled provider: it costs the same as a
question, and reads nothing from anyone's library.
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status
from primer_anomaly import SequenceScore, score_truncated
from primer_contracts.errors import ErrorCode
from primer_service.db import get_session
from primer_service.durable import DurableRoute
from primer_service.errors import ProblemError
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from primer_chat import scoring
from primer_chat.config import Settings
from primer_chat.identity import CurrentPrincipal
from primer_chat.providers_store import ProviderStore

router = APIRouter(prefix="/api/v1", tags=["scoring"], route_class=DurableRoute)

Session = Annotated[AsyncSession, Depends(get_session)]

#: The most alternatives the OpenAI API allows per token, and vLLM's default
#: `--max-logprobs`. More would be refused by most servers that exist.
MAX_TOP_K = 20

#: `generate` scores what the model writes in answer to `text`; `text`
#: scores `text` itself.
ScoreMode = Literal["generate", "text"]


class ScoreRequest(BaseModel):
    """What to score, and against which model.

    Not a `WireModel`, which strips whitespace from every string: the text
    being scored is the text that was given, and a leading space is a token
    of its own.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    #: Both required. A model is only fully named by the pair, as in `/models`.
    provider_id: UUID
    model: str = Field(min_length=1, max_length=200)
    mode: ScoreMode
    text: str = Field(min_length=1, max_length=20_000)
    top_k: int = Field(default=MAX_TOP_K, ge=1, le=MAX_TOP_K)
    #: How long an answer to generate. Ignored when scoring supplied text.
    max_tokens: int = Field(default=256, ge=1, le=2048)


class ScoredText(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_id: UUID
    model: str
    mode: ScoreMode
    #: The answer that was generated, or the text as supplied.
    text: str
    #: Always a truncated estimate: `estimate` says so and `top_k` says how
    #: much of each distribution was seen.
    score: SequenceScore


@router.post("/score", summary="Score a model's surprise at each token")
async def score(
    payload: ScoreRequest, principal: CurrentPrincipal, request: Request, session: Session
) -> ScoredText:
    del principal
    settings: Settings = request.app.state.settings
    store = ProviderStore(session, settings, request.app.state.secret_box)
    provider = await store.find(payload.provider_id)
    if provider is None or not provider.enabled:
        raise ProblemError(
            code=ErrorCode.NOT_FOUND,
            title="Provider not found",
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That provider is not available on this deployment.",
        )
    if payload.mode == "generate":
        scorable = await scoring.generate(
            provider,
            payload.model,
            payload.text,
            max_tokens=payload.max_tokens,
            top_k=payload.top_k,
            timeout_seconds=settings.chat_timeout_seconds,
        )
    else:
        scorable = await scoring.score_text(
            provider,
            payload.model,
            payload.text,
            top_k=payload.top_k,
            timeout_seconds=settings.chat_timeout_seconds,
        )
    return ScoredText(
        provider_id=provider.id,
        model=payload.model,
        mode=payload.mode,
        text=scorable.text,
        score=score_truncated(scorable.tokens),
    )
