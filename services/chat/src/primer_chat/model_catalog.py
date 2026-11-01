"""What every configured provider is serving, gathered into one list.

Asked live rather than kept as a list of Primer's own, so a model pulled on
a workstation shows up without redeploying Primer to match.

Providers are asked in parallel and each is allowed to fail on its own. A
deployment with a hosted API and a machine at home that is currently asleep
can still answer: the models it can reach are offered, and the ones it
cannot are named so an operator knows which half is missing. That is the
distinction the whole module exists to preserve - "nothing is available" and
"one of three is unavailable" need different reactions, and collapsing them
is how an interface ends up lying about a deployment's health.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import httpx2
from primer_contracts.chat import ChatModel, ChatModelList

from primer_chat.providers_store import ResolvedProvider

logger = logging.getLogger(__name__)

#: Long enough for a cold local server to answer, short enough that one
#: unreachable provider does not hold up a page load. Every provider is
#: asked at once, so this is the whole wait, not the wait per provider.
LISTING_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class ProviderModels:
    """What one provider answered, or why it did not."""

    provider: ResolvedProvider
    models: tuple[str, ...] = ()
    error: str | None = None


async def models_of(provider: ResolvedProvider) -> ProviderModels:
    """Ask one provider what it serves. Never raises."""
    if provider.key_error:
        # No point calling: the request would go out unauthenticated and come
        # back as a permission error that says nothing about the real fault.
        return ProviderModels(provider, error=provider.key_error)
    try:
        async with httpx2.AsyncClient(timeout=LISTING_TIMEOUT_SECONDS) as client:
            response = await client.get(
                f"{provider.base_url.rstrip('/')}/models",
                headers={"Authorization": f"Bearer {provider.api_key or 'none'}"},
            )
        response.raise_for_status()
        served = tuple(entry["id"] for entry in response.json().get("data", []) if entry.get("id"))
    except httpx2.RequestError as error:
        # Not there is an operational fact, not a bug, and this runs on every
        # load of the chat screen. One line, no trace.
        logger.warning(
            "provider %s at %s could not be reached (%s)",
            provider.name,
            provider.base_url,
            type(error).__name__,
        )
        return ProviderModels(provider, error=f"{provider.base_url} could not be reached.")
    except Exception:
        logger.warning("provider %s did not list models", provider.name, exc_info=True)
        return ProviderModels(provider, error=f"{provider.base_url} did not answer usefully.")

    if not served:
        return ProviderModels(provider, error=f"{provider.name} is serving no models.")
    return ProviderModels(provider, models=served)


async def catalog(
    providers: list[ResolvedProvider], *, preferred_model: str | None
) -> ChatModelList:
    """Every model every provider serves, and what could not be reached.

    The default is the deployment's configured model when a provider actually
    serves it, and otherwise simply the first model available. Naming a
    default nothing serves is how the picker used to offer a choice that did
    not exist, and it is no less wrong for being the operator's own setting -
    which is why the chart's model is no longer required to be set at all.
    """
    if not providers:
        return ChatModelList(
            models=(),
            endpoint_reachable=False,
            detail="No inference provider is configured for this deployment.",
        )

    answered = await asyncio.gather(*(models_of(provider) for provider in providers))
    unreachable = tuple(result.provider.name for result in answered if result.error)

    offered: list[ChatModel] = []
    for result in answered:
        for name in result.models:
            offered.append(
                ChatModel(
                    id=name,
                    default=False,
                    provider_id=result.provider.id,
                    provider_name=result.provider.name,
                    supports_logprobs=result.provider.supports_logprobs,
                    supports_prompt_logprobs=result.provider.supports_prompt_logprobs,
                )
            )

    if not offered:
        # Every provider failed. The first reason is the useful one when
        # there is only one provider, which is the common case.
        detail = next(
            (result.error for result in answered if result.error),
            "No provider is serving any model.",
        )
        return ChatModelList(
            models=(), endpoint_reachable=False, detail=detail, unreachable=unreachable
        )

    chosen = next((model for model in offered if model.id == preferred_model), offered[0])
    models = tuple(model.model_copy(update={"default": model is chosen}) for model in offered)
    return ChatModelList(
        models=models,
        endpoint_reachable=True,
        # Said only when something is wrong. A deployment answering from
        # every provider it has needs no commentary.
        detail=(
            f"{len(unreachable)} of {len(providers)} providers could not be reached."
            if unreachable
            else None
        ),
        unreachable=unreachable,
    )
