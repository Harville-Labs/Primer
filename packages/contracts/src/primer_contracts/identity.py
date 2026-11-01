"""The internal principal that services authorize against.

`Principal` is derived by the Control API from trusted edge identity. It is
never populated directly from a browser-supplied header by any other service.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import Field

from primer_contracts.base import WireModel


class Principal(WireModel):
    """An authenticated Primer identity acting on a request."""

    subject: str = Field(min_length=1, max_length=255, description="Stable OIDC `sub` claim")
    user_id: UUID = Field(description="Primer-internal user identifier")
    groups: tuple[str, ...] = Field(
        default=(), description="Operator-policy groups; never an implicit grant to resources"
    )
    email: str | None = Field(default=None, max_length=320)
    display_name: str | None = Field(default=None, max_length=255)


def is_admin(principal: Principal, *, auth_enabled: bool, admin_group: str | None) -> bool:
    """Whether this principal may see and change how the deployment is wired.

    One rule, shared, because two services enforce it and a deployment where
    they disagreed about who is an administrator would be a deployment whose
    answer depends on which door you knocked on.

    Group membership is the whole test. Primer implements no authentication
    and no roles of its own; the operator names a group in their identity
    provider, the proxy asserts it, and this is where that assertion is read.
    It stays consistent with `groups` never granting access to a *resource* -
    no library becomes readable because of this, only the deployment's own
    configuration.

    Fails closed. Authentication on with no group named means nobody is an
    administrator, which is the safe reading of an operator who has not made
    the decision yet: the alternative is that every user of a shared
    deployment can repoint it at an endpoint of their own.

    With authentication off there is exactly one identity and it is whoever
    is running Primer - a single-user Compose stack, by construction. There
    is nobody to withhold this from, and withholding it would leave that
    deployment with no way to reach its own settings at all.
    """
    if not auth_enabled:
        return True
    if not admin_group:
        return False
    return admin_group in principal.groups


class DeploymentCapabilities(WireModel):
    """What this deployment can actually do, as the browser needs to know it.

    Sent to the web app so it can hide what will not work rather than
    offering it and failing. Every field is a checked fact or an operator's
    declaration - none is inferred from a model's name.
    """

    #: False means every request is one fixed local user. The interface warns
    #: about this permanently: it is the difference between a private
    #: notebook and an open one.
    auth_enabled: bool
    #: Nothing can be indexed without embeddings, so nothing can be asked.
    ingestion_available: bool
    chat_available: bool
    tools_available: bool
    #: Lab is optional and off by default; without it the Lab section is not shown.
    lab_available: bool = False
    max_upload_bytes: int = Field(ge=0)
    supported_extensions: tuple[str, ...] = ()
    #: Whether the caller may reach the deployment's own settings. Reported
    #: rather than guessed at in the browser, which cannot see the group
    #: policy and must not be the thing deciding it either.
    is_admin: bool = False
