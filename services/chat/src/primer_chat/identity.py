"""The same identity boundary the Control API uses.

Chat trusts headers injected by the authenticating proxy and derives the
same internal user id from the same subject, so a conversation started
through one service is owned by the same person in the other.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from primer_contracts.identity import Principal
from primer_service.identity import ensure_admin, principal_from_request

from primer_chat.config import Settings


def get_principal(request: Request) -> Principal:
    return principal_from_request(request, request.app.state.settings)


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]


def require_admin(request: Request, principal: CurrentPrincipal) -> Principal:
    settings: Settings = request.app.state.settings
    return ensure_admin(principal, settings, restricted="Changing this deployment's settings")


CurrentAdmin = Annotated[Principal, Depends(require_admin)]
