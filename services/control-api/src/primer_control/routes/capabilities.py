"""What this deployment can do, for the interface that has to reflect it.

The web app asks this once and hides what will not work, rather than
offering a feature and failing when someone uses it. Everything reported
here is a configured fact, not a guess: whether authentication is on,
whether a broker exists to ingest with, what the upload limit is.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from primer_contracts.identity import DeploymentCapabilities, is_admin
from primer_storage import SUPPORTED_EXTENSIONS

from primer_control.config import Settings
from primer_control.identity import CurrentPrincipal

router = APIRouter(prefix="/api/v1", tags=["capabilities"])


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


Config = Annotated[Settings, Depends(get_settings)]


@router.get("/capabilities", summary="What this deployment supports")
async def capabilities(principal: CurrentPrincipal, settings: Config) -> DeploymentCapabilities:
    """Report the deployment's shape.

    Authenticated, because the answer describes a specific deployment's
    configuration and there is no reason for it to be readable by anyone who
    cannot already use it.

    Chat, Lab and tool availability are reported by Control rather than probed
    here: Control does not hold the inference configuration, and asking Chat
    on every page load would make a browser refresh depend on a model
    endpoint being awake.
    """
    return DeploymentCapabilities(
        auth_enabled=settings.auth_enabled,
        # Without a broker an upload is accepted and then sits queued
        # forever, which the interface should say plainly rather than
        # letting a user wonder.
        ingestion_available=bool(settings.broker_url),
        chat_available=bool(settings.chat_service_url),
        tools_available=settings.tools_enabled,
        lab_available=bool(settings.lab_service_url),
        max_upload_bytes=settings.max_upload_bytes,
        supported_extensions=tuple(sorted(SUPPORTED_EXTENSIONS)),
        # Reported rather than left for the browser to work out: it cannot
        # see the group policy, and it must not be the thing deciding this
        # even for hiding a link.
        is_admin=is_admin(
            principal,
            auth_enabled=settings.auth_enabled,
            admin_group=settings.admin_group,
        ),
    )
