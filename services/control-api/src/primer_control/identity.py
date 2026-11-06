"""Turn trusted edge identity into an internal `Principal`.

The edge (`oauth2-proxy`) strips client-supplied identity headers and injects
its own after authenticating; the web app forwards them with its credential.
`primer_service.identity` decides whether to believe them.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from primer_contracts.identity import Principal
from primer_service.identity import ensure_admin, principal_from_request

from primer_control.config import Settings


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_principal(
    request: Request, settings: Annotated[Settings, Depends(get_settings)]
) -> Principal:
    """FastAPI dependency yielding the acting principal."""
    return principal_from_request(request, settings)


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]


def require_admin(
    principal: CurrentPrincipal, settings: Annotated[Settings, Depends(get_settings)]
) -> Principal:
    return ensure_admin(principal, settings, restricted="Seeing this deployment's settings")


CurrentAdmin = Annotated[Principal, Depends(require_admin)]
