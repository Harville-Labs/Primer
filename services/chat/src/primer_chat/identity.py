"""The same identity boundary the Control API uses.

Chat trusts headers injected by the authenticating proxy and derives the
same internal user id from the same subject, so a conversation started
through one service is owned by the same person in the other.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request, status
from primer_contracts.errors import ErrorCode
from primer_contracts.identity import Principal, is_admin
from primer_service.errors import ProblemError
from primer_service.identity import principal_from_request

from primer_chat.config import Settings


def get_principal(request: Request) -> Principal:
    return principal_from_request(request, request.app.state.settings)


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]


def require_admin(request: Request, principal: CurrentPrincipal) -> Principal:
    """Refuse anyone who may not change how this deployment is wired.

    403 rather than the 404 used for libraries. Absence and denial are worth
    conflating for a resource whose existence is itself private; a
    deployment's settings page is not private in that way, and telling an
    ordinary user "not for you" is more useful than pretending the page does
    not exist.
    """
    settings: Settings = request.app.state.settings
    allowed = is_admin(
        principal,
        auth_enabled=settings.auth_mode == "oidc",
        admin_group=settings.admin_group,
    )
    if not allowed:
        raise ProblemError(
            code=ErrorCode.IDENTITY_INVALID,
            title="Not an administrator",
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Changing this deployment's settings is restricted to administrators.",
        )
    return principal


CurrentAdmin = Annotated[Principal, Depends(require_admin)]
