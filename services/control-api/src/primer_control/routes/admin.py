"""What this deployment is wired to, and whether any of it is answering.

An operator debugging a self-hosted install has to know two things that are
otherwise only visible in a values file and a log: what Primer was pointed
at, and which of those are currently reachable. Both live in different
processes, so without this the answer means reading several sets of
container logs and inferring.

Restricted to administrators. A URL is not a secret, but the set of them is
a map of the deployment's inside, and an ordinary user has no use for it.

Credentials never appear here, not even redacted. A URL that carries one in
its userinfo is reported with that part removed rather than starred out: a
masked secret is still a statement about its length and shape.
"""

from __future__ import annotations

import asyncio
from typing import Annotated
from urllib.parse import urlsplit, urlunsplit

import httpx2
from fastapi import APIRouter, Depends, Request, status
from primer_contracts.deployment import DependencyStatus, DeploymentStatus
from primer_contracts.studio import StudioUsage
from primer_service.db import get_session
from primer_service.durable import DurableRoute
from sqlalchemy.ext.asyncio import AsyncSession

from primer_control.config import Settings
from primer_control.health import DependencyRegistry
from primer_control.identity import CurrentAdmin
from primer_control.repositories.studio import StudioRepository

router = APIRouter(prefix="/api/v1/admin", tags=["admin"], route_class=DurableRoute)

#: Short. This runs to render a page, and a dependency that is wedged should
#: show as unreachable quickly rather than hold the whole page open.
PROBE_TIMEOUT_SECONDS = 5.0


def get_dependencies(request: Request) -> DependencyRegistry:
    registry: DependencyRegistry = request.app.state.dependencies
    return registry


def without_credentials(url: str | None) -> str | None:
    """A URL safe to put on a screen.

    Anything in the userinfo is removed rather than masked. A masked
    credential still says how long it was, and a screenshot of a settings
    page is a thing people paste into issues.
    """
    if not url:
        return None
    parts = urlsplit(url)
    if not parts.netloc or "@" not in parts.netloc:
        return url
    host = parts.netloc.rsplit("@", 1)[1]
    return urlunsplit((parts.scheme, host, parts.path, parts.query, parts.fragment))


async def reachable(url: str, path: str = "/health/ready") -> tuple[bool, str]:
    """Whether a Primer service answers, and what it said if not."""
    try:
        async with httpx2.AsyncClient(timeout=PROBE_TIMEOUT_SECONDS) as client:
            response = await client.get(f"{url.rstrip('/')}{path}")
    except httpx2.RequestError as error:
        return False, f"Could not be reached ({type(error).__name__})."
    if response.status_code >= 500:
        return False, f"Answered {response.status_code}, so it is not ready."
    return True, "Answering."


@router.get("/status", summary="What this deployment is wired to")
async def deployment_status(
    admin: CurrentAdmin,
    request: Request,
    dependencies: Annotated[DependencyRegistry, Depends(get_dependencies)],
) -> DeploymentStatus:
    """Every configured connection, and whether it is currently answering.

    Control's own dependencies are asked through the same registry the
    readiness probe uses, so this page and Kubernetes cannot disagree about
    whether the database is up. The other services are probed over HTTP,
    which is the only way one process learns about another.
    """
    del admin
    settings: Settings = request.app.state.settings

    checks = await dependencies.evaluate()
    reported = [
        DependencyStatus(
            name=name,
            #: Control's own dependencies. The URL is shown for the ones an
            #: operator configured; a check with no matching setting simply
            #: reports its state.
            url=without_credentials(_url_for(name, settings)),
            reachable=ok,
            detail="Answering." if ok else "Not answering.",
        )
        for name, ok in checks.items()
    ]

    # Only Chat: Control does not talk to Retrieval, and reporting on a
    # connection this service does not hold would be reporting a guess.
    # Chat's own dependencies belong on Chat's own status.
    services = [(name, url) for name, url in (("chat", settings.chat_service_url),) if url]
    probed = await asyncio.gather(*(reachable(url) for _, url in services))
    reported.extend(
        DependencyStatus(
            name=name,
            url=without_credentials(url),
            reachable=ok,
            detail=detail,
        )
        for (name, url), (ok, detail) in zip(services, probed, strict=True)
    )

    return DeploymentStatus(
        auth_mode=settings.auth_mode,
        admin_group=settings.admin_group,
        dependencies=tuple(reported),
        vision_configured=settings.vision_enabled,
    )


@router.get("/studio/usage", summary="How much Studio storage each person uses")
async def studio_usage(
    admin: CurrentAdmin, session: Annotated[AsyncSession, Depends(get_session)]
) -> list[StudioUsage]:
    """Counts and bytes per person. Never the images: see `StudioAccess`."""
    del admin
    return await StudioRepository(session).usage()


def _url_for(name: str, settings: Settings) -> str | None:
    """The configured address behind one of Control's own checks."""
    return {
        "database": settings.database_url,
        "broker": settings.broker_url,
        "sources": settings.source_store_url,
    }.get(name)


__all__ = ["router", "status", "without_credentials"]
