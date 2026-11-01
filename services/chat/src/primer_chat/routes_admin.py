"""Managing the endpoints this deployment answers from.

Restricted to administrators, because these routes decide where every user's
questions are sent and what credential goes with them. The rule itself lives
in the contracts package, shared with Control, so the two services cannot
disagree about who counts as one.

No route returns an API key. A stored key can be replaced or removed, and a
caller can learn whether one is held; it cannot be read back. A key a page
can display is a key a screenshot, a cache, or a browser extension can carry
away, and it is usually somebody's paid account with a third party.

The provider configured in the chart is listed here but cannot be edited: it
lives in the environment and changes by redeploying. Letting it be edited
would put a deployment's own configuration in two places that could then
disagree about which one is in force.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from primer_contracts.errors import ErrorCode
from primer_contracts.providers import (
    ProviderCheck,
    ProviderCreate,
    ProviderSummary,
    ProviderUpdate,
)
from primer_service.db import get_session
from primer_service.durable import DurableRoute
from primer_service.errors import ProblemError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from primer_chat.config import Settings
from primer_chat.identity import CurrentAdmin
from primer_chat.model_catalog import models_of
from primer_chat.models import Provider
from primer_chat.providers_store import ProviderStore, ResolvedProvider
from primer_chat.secrets import SecretBox, SecretsUnavailable

router = APIRouter(prefix="/api/v1/admin", tags=["admin"], route_class=DurableRoute)

Session = Annotated[AsyncSession, Depends(get_session)]


def store_for(request: Request, session: AsyncSession) -> ProviderStore:
    settings: Settings = request.app.state.settings
    return ProviderStore(session, settings, request.app.state.secret_box)


def not_found() -> ProblemError:
    return ProblemError(
        code=ErrorCode.NOT_FOUND,
        title="Provider not found",
        status_code=status.HTTP_404_NOT_FOUND,
        detail="No provider with that identifier exists on this deployment.",
    )


def immutable() -> ProblemError:
    """The chart's own provider is configuration, not data."""
    return ProblemError(
        code=ErrorCode.VALIDATION_FAILED,
        title="Provider is part of this deployment",
        status_code=status.HTTP_409_CONFLICT,
        detail=(
            "This provider comes from the deployment's own configuration. "
            "Change it where that is set, not here."
        ),
    )


def duplicate_name(name: str) -> ProblemError:
    return ProblemError(
        code=ErrorCode.CONFLICT,
        title="Name already used",
        status_code=status.HTTP_409_CONFLICT,
        detail=f"Another provider is already called {name!r}.",
    )


def sealed_or_refused(box: SecretBox, api_key: str | None) -> str | None:
    """Encrypt a key, or explain why this deployment cannot hold one.

    Refused rather than stored in the clear. An operator who has not
    configured an encryption key has not agreed to Primer keeping a
    third-party credential in its database.
    """
    if not api_key:
        return None
    try:
        return box.seal(api_key)
    except SecretsUnavailable as error:
        raise ProblemError(
            code=ErrorCode.VALIDATION_FAILED,
            title="Cannot store an API key",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error


@router.get("/providers", summary="Every endpoint this deployment can ask")
async def list_providers(
    admin: CurrentAdmin, request: Request, session: Session
) -> list[ProviderSummary]:
    del admin
    return [provider.summary for provider in await store_for(request, session).all()]


@router.post("/providers", status_code=status.HTTP_201_CREATED, summary="Add an endpoint")
async def add_provider(
    payload: ProviderCreate, admin: CurrentAdmin, request: Request, session: Session
) -> ProviderSummary:
    del admin
    store = store_for(request, session)
    row = Provider(
        id=uuid.uuid4(),
        name=payload.name,
        base_url=payload.base_url,
        api_key_sealed=sealed_or_refused(request.app.state.secret_box, payload.api_key),
        enabled=payload.enabled,
        supports_logprobs=payload.supports_logprobs,
        supports_prompt_logprobs=payload.supports_prompt_logprobs,
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError as error:
        raise duplicate_name(payload.name) from error
    await session.refresh(row)
    return store.resolve(row).summary


@router.patch("/providers/{provider_id}", summary="Change an endpoint")
async def update_provider(
    provider_id: uuid.UUID,
    payload: ProviderUpdate,
    admin: CurrentAdmin,
    request: Request,
    session: Session,
) -> ProviderSummary:
    del admin
    store = store_for(request, session)
    row = await store.get(provider_id)
    if row is None:
        # Either it never existed, or it is the deployment's own - which has
        # no row and cannot be edited. The two are told apart so an operator
        # is pointed at the values file rather than at a typo.
        raise immutable() if await store.find(provider_id) else not_found()

    if payload.name is not None:
        row.name = payload.name
    if payload.base_url is not None:
        row.base_url = payload.base_url
    if payload.enabled is not None:
        row.enabled = payload.enabled
    if payload.supports_logprobs is not None:
        row.supports_logprobs = payload.supports_logprobs
    if payload.supports_prompt_logprobs is not None:
        row.supports_prompt_logprobs = payload.supports_prompt_logprobs
    if payload.api_key is not None:
        # Three states, not two: an empty string removes the stored key,
        # which is the only way to unset one that has been set.
        row.api_key_sealed = sealed_or_refused(request.app.state.secret_box, payload.api_key)

    try:
        await session.flush()
    except IntegrityError as error:
        raise duplicate_name(payload.name or row.name) from error
    await session.refresh(row)
    return store.resolve(row).summary


@router.delete(
    "/providers/{provider_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove an endpoint",
)
async def delete_provider(
    provider_id: uuid.UUID, admin: CurrentAdmin, request: Request, session: Session
) -> Response:
    del admin
    store = store_for(request, session)
    row = await store.get(provider_id)
    if row is None:
        raise immutable() if await store.find(provider_id) else not_found()
    await session.delete(row)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/providers/{provider_id}/check", summary="Ask an endpoint what it serves")
async def check_provider(
    provider_id: uuid.UUID, admin: CurrentAdmin, request: Request, session: Session
) -> ProviderCheck:
    """Try the endpoint now, and report what came back.

    On demand rather than on a schedule, because the useful moment to learn
    that a URL is wrong is while it is still on screen being typed.
    """
    del admin
    provider = await store_for(request, session).find(provider_id)
    if provider is None:
        raise not_found()
    return await checked(provider)


async def checked(provider: ResolvedProvider) -> ProviderCheck:
    result = await models_of(provider)
    if result.error:
        return ProviderCheck(ok=False, detail=result.error)
    return ProviderCheck(
        ok=True,
        detail=f"Serving {len(result.models)} model{'' if len(result.models) == 1 else 's'}.",
        models=result.models,
    )
