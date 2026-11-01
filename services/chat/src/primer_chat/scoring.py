"""How surprised a provider's model was by each token, from its logprobs.

Chat does this rather than Lab because Chat holds the providers and their
keys. The maths is `primer_anomaly`'s; this module only gets the numbers out
of an endpoint and says, specifically, when it cannot.

Two ways to get them, because they answer different questions. Generating
asks how sure the model was of what it wrote itself. Scoring supplied text
asks how surprised it is by someone else's - which is the one an evaluation
wants, and the one fewer servers can do.

Whatever the endpoint returns is the top k at most, so every score here is
a truncated estimate and says so (`EntropyEstimate.TRUNCATED`, with `top_k`).
Entropy over twenty tokens is a lower bound, not the model's uncertainty.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import httpx2
from fastapi import status
from primer_anomaly import TopLogprobs
from primer_contracts.errors import ErrorCode
from primer_service.errors import ProblemError

from primer_chat.providers_store import ResolvedProvider


@dataclass(frozen=True)
class Scorable:
    """What came back: the text, and its tokens with their logprobs."""

    text: str
    tokens: list[TopLogprobs]


def undeclared(provider: ResolvedProvider, flag: str) -> ProblemError:
    """The operator has not said this provider can do it, so it is not tried.

    Refused rather than attempted. A server that does not support logprobs
    usually answers anyway with the field left out, and the person reading
    the result would see a model that was never surprised.
    """
    # The deployment's own is declared where the rest of it is configured,
    # not on the settings page, which cannot edit it.
    where = (
        f"set PRIMER_CHAT_{flag.upper()}=true for this deployment"
        if provider.source == "deployment"
        else f"declare {flag} for it in settings"
    )
    return ProblemError(
        code=ErrorCode.VALIDATION_FAILED,
        title="Provider does not declare logprobs",
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=f"{provider.name} is not declared to return logprobs. If its server does, {where}.",
    )


def missing(provider: ResolvedProvider, flag: str) -> ProblemError:
    """Declared, asked for, and not returned: the declaration is wrong.

    Not worth retrying. The same server will leave them out again, and the
    fix is either the declaration or the server's own configuration.
    """
    return ProblemError(
        code=ErrorCode.DEPENDENCY_UNAVAILABLE,
        title="Provider returned no logprobs",
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=(
            f"{provider.name} declares {flag} but answered without logprobs. "
            "Trying again will not change that: turn the declaration off in settings, "
            "or enable logprobs on the server."
        ),
    )


def nothing_to_score() -> ProblemError:
    return ProblemError(
        code=ErrorCode.VALIDATION_FAILED,
        title="Nothing to score",
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=(
            "There were no tokens to score. Text needs at least two tokens, because "
            "the first has nothing before it to be predicted from."
        ),
    )


def unreachable(provider: ResolvedProvider) -> ProblemError:
    return ProblemError(
        code=ErrorCode.DEPENDENCY_UNAVAILABLE,
        title="Provider could not be reached",
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=f"{provider.base_url} could not be reached. It may be starting; try again.",
    )


def refused(provider: ResolvedProvider, code: int) -> ProblemError:
    """The endpoint answered with an error status.

    Its body is not quoted, for the reason `failures` gives: it is written by
    whatever server answered and may echo the credential it rejected. The
    likeliest 400 is named instead, because it is the one an operator can fix.
    """
    return ProblemError(
        code=ErrorCode.DEPENDENCY_UNAVAILABLE,
        title="Provider refused the request",
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=(
            f"{provider.name} answered {code}. "
            + (
                "It may allow fewer top logprobs than were asked for: vLLM's "
                "--max-logprobs is 20 by default. Ask for fewer, or raise it."
                if code == status.HTTP_400_BAD_REQUEST
                else "Check that the model is served there and the key is right."
            )
        ),
    )


def generated_tokens(body: Mapping[str, Any]) -> Scorable | None:
    """The tokens of a chat completion, or None if it carried no logprobs.

    The OpenAI shape: `choices[0].logprobs.content`, one entry per token
    with its own `top_logprobs`.
    """
    choice = (body.get("choices") or [{}])[0]
    text = (choice.get("message") or {}).get("content") or ""
    content = (choice.get("logprobs") or {}).get("content")
    if content is None:
        return None
    tokens = [
        TopLogprobs(
            token=entry["token"],
            logprob=entry["logprob"],
            top_logprobs=[top["logprob"] for top in entry.get("top_logprobs") or ()],
        )
        for entry in content
    ]
    if text and not tokens:
        return None
    if any(not token.top_logprobs for token in tokens):
        return None
    return Scorable(text=text, tokens=tokens)


def prompt_tokens(body: Mapping[str, Any], text: str) -> Scorable | None:
    """The supplied text's own tokens, from a completion that echoed it.

    The legacy completions shape: parallel `tokens`, `token_logprobs` and
    `top_logprobs` lists covering the prompt and then whatever was
    generated after it. The prompt's length comes from `usage`, since the
    generated tail is not marked. The first token has no logprob - nothing
    came before it - and is left out rather than scored as certain.
    """
    choice = (body.get("choices") or [{}])[0]
    logprobs = choice.get("logprobs") or {}
    names: Sequence[str] | None = logprobs.get("tokens")
    values: Sequence[float | None] | None = logprobs.get("token_logprobs")
    tops: Sequence[Mapping[str, float] | None] | None = logprobs.get("top_logprobs")
    length = (body.get("usage") or {}).get("prompt_tokens")
    if not names or values is None or tops is None or not isinstance(length, int):
        return None
    tokens: list[TopLogprobs] = []
    for name, value, top in list(zip(names, values, tops, strict=False))[:length]:
        if value is None:
            continue
        if not top:
            return None
        tokens.append(TopLogprobs(token=name, logprob=value, top_logprobs=list(top.values())))
    return Scorable(text=text, tokens=tokens)


async def _post(
    provider: ResolvedProvider, path: str, payload: dict[str, Any], timeout_seconds: float
) -> Mapping[str, Any]:
    try:
        async with httpx2.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.post(
                f"{provider.base_url.rstrip('/')}{path}",
                json=payload,
                headers={"Authorization": f"Bearer {provider.api_key or 'none'}"},
            )
    except httpx2.RequestError as error:
        raise unreachable(provider) from error
    if response.is_error:
        raise refused(provider, response.status_code)
    return response.json()


async def generate(
    provider: ResolvedProvider,
    model: str,
    prompt: str,
    *,
    max_tokens: int,
    top_k: int,
    timeout_seconds: float,
) -> Scorable:
    """Have the model answer `prompt`, and return its answer's logprobs."""
    if not provider.supports_logprobs:
        raise undeclared(provider, "supports_logprobs")
    body = await _post(
        provider,
        "/chat/completions",
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "logprobs": True,
            "top_logprobs": top_k,
        },
        timeout_seconds,
    )
    scorable = generated_tokens(body)
    if scorable is None:
        raise missing(provider, "supports_logprobs")
    if not scorable.tokens:
        raise nothing_to_score()
    return scorable


async def score_text(
    provider: ResolvedProvider, model: str, text: str, *, top_k: int, timeout_seconds: float
) -> Scorable:
    """Return the logprobs the model gives `text`, which it did not write.

    Through `echo` on `/completions` rather than vLLM's own `prompt_logprobs`
    field. That field returns each position's top k keyed by token id, with
    the prompt's own token among them and nothing to say which one it is,
    so it cannot be read without the tokenizer. `echo` names the token.
    One token is generated because some servers refuse to generate none;
    it is cut off by length.
    """
    if not provider.supports_prompt_logprobs:
        raise undeclared(provider, "supports_prompt_logprobs")
    body = await _post(
        provider,
        "/completions",
        {
            "model": model,
            "prompt": text,
            "max_tokens": 1,
            "echo": True,
            "logprobs": top_k,
        },
        timeout_seconds,
    )
    scorable = prompt_tokens(body, text)
    if scorable is None:
        raise missing(provider, "supports_prompt_logprobs")
    if not scorable.tokens:
        raise nothing_to_score()
    return scorable
