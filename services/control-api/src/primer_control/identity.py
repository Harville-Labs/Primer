"""Turn trusted edge identity into an internal `Principal`.

The edge (`oauth2-proxy`) strips client-supplied identity headers and injects
its own after authenticating. This module therefore trusts the configured
headers in OIDC mode and, critically, ignores them entirely in disabled mode
so a spoofed header cannot manufacture an identity in a local deployment.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request, status
from primer_contracts.errors import ErrorCode
from primer_contracts.identity import Principal, is_admin
from primer_service.errors import ProblemError
from primer_service.identity import principal_from_request

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
    """Refuse anyone who may not see how this deployment is wired.

    403 rather than the 404 used for libraries. Absence and denial are worth
    conflating for a resource whose existence is itself private; a settings
    page is not private in that way, and telling an ordinary user "not for
    you" is more useful than pretending it does not exist.
    """
    if not is_admin(
        principal, auth_enabled=settings.auth_enabled, admin_group=settings.admin_group
    ):
        raise ProblemError(
            code=ErrorCode.IDENTITY_INVALID,
            title="Not an administrator",
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Seeing this deployment's settings is restricted to administrators.",
        )
    return principal


CurrentAdmin = Annotated[Principal, Depends(require_admin)]
