"""Who is asking, and whether they may change the model.

Lab trusts the same proxy headers as Control and Chat and reads them the
same way; only the question it asks of the result is its own.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from primer_contracts.identity import Principal, is_admin
from primer_service.identity import principal_from_request

from primer_lab.config import Settings


def get_principal(request: Request) -> Principal:
    return principal_from_request(request, request.app.state.settings)


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]


def can_choose_model(settings: Settings, principal: Principal) -> bool:
    """Whether this person may replace the model every other user is using."""
    match settings.lab_model_choice:
        case "users":
            return True
        case "admins":
            return is_admin(
                principal,
                auth_enabled=settings.auth_mode == "oidc",
                admin_group=settings.admin_group,
            )
        case "operator":
            return False
