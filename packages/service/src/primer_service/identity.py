"""Turn the headers the authenticating proxy set into a `Principal`.

Every service that answers a user derives the same identity from the same
subject, so a library created through Control, a conversation in Chat, and
a run in Lab all belong to the same person. Control and Chat each held a
copy of this, identical down to the namespace UUID; Lab would have been the
third, and a third copy is the one that ends up with a different namespace
and gives the same person two identities.

What stays in each service is what it guards with this: which routes need
an administrator, and how it words a refusal.

The headers are believed only when the request also carries the web app's
credential. The proxy strips inbound copies of them at the edge, but inside
the cluster nothing did: anything that could reach a service's port could
name any subject and be that person. Only the web app is given the
credential, so only requests it forwarded carry an identity.
"""

from __future__ import annotations

import uuid
from typing import Literal, Self

from fastapi import Request, status
from primer_contracts.errors import ErrorCode
from primer_contracts.identity import Principal, is_admin
from pydantic import BaseModel, Field, SecretStr, model_validator

from primer_service.credentials import WEB_TOKEN_HEADER, presents
from primer_service.errors import ProblemError

#: Namespace for deriving stable user UUIDs from OIDC subjects. Changing it
#: changes every user's id, and with it who owns every library.
PRIMER_USER_NAMESPACE = uuid.UUID("6f2f5a10-9f4b-5f0e-9b53-7a1a5c6d8e10")

LOCAL_SUBJECT = "primer-local-user"


AuthMode = Literal["disabled", "oidc"]


class IdentitySettings(BaseModel):
    """How a service reads identity, under the same names in every service.

    Mixed into each service's settings, so `PRIMER_SUBJECT_HEADER` means the
    same thing to Control, Chat and Lab, and the web app reads the same
    variables to decide which headers to forward.
    """

    auth_mode: AuthMode = "disabled"
    subject_header: str = Field(
        default="X-Forwarded-User",
        description="Edge-injected header carrying the stable OIDC subject",
    )
    email_header: str = Field(default="X-Forwarded-Email")
    groups_header: str = Field(default="X-Forwarded-Groups")
    groups_delimiter: str = Field(default=",", min_length=1)
    #: The group whose members may see and change how this deployment is
    #: wired. Unset with authentication on means nobody, which is the safe
    #: reading of an operator who has not decided yet.
    admin_group: str | None = Field(default=None)
    web_token: SecretStr | None = Field(
        default=None,
        description="Credential the web app presents with identity headers; required for oidc",
    )

    @model_validator(mode="after")
    def _oidc_requires_headers_and_web_token(self) -> Self:
        """OIDC mode without these would authenticate nobody, or anybody.

        Failing at startup is the only safe outcome. Without a subject header
        every user is rejected; without the web token there is nothing to tell
        a forwarded request from one made up inside the cluster.
        """
        if self.auth_mode != "oidc":
            return self
        if not self.subject_header.strip():
            raise ValueError("auth_mode='oidc' requires a non-empty subject_header")
        if self.web_token is None or not self.web_token.get_secret_value():
            raise ValueError(
                "auth_mode='oidc' requires PRIMER_WEB_TOKEN, the credential the web app "
                "presents with identity headers. The chart generates it; set the same "
                "value on the web app and on Control, Chat and Lab."
            )
        return self

    @property
    def auth_enabled(self) -> bool:
        return self.auth_mode == "oidc"


def user_id_for_subject(subject: str) -> uuid.UUID:
    """Derive a stable Primer user id from an OIDC subject."""
    return uuid.uuid5(PRIMER_USER_NAMESPACE, subject)


def principal_from_request(request: Request, settings: IdentitySettings) -> Principal:
    """The principal acting on this request.

    With authentication off the headers are ignored entirely, not defaulted:
    a local deployment must not behave differently because someone sent a
    header, or a spoofed one could manufacture an identity.
    """
    if settings.auth_mode != "oidc":
        return Principal(subject=LOCAL_SUBJECT, user_id=user_id_for_subject(LOCAL_SUBJECT))

    if not presents(request, WEB_TOKEN_HEADER, settings.web_token):
        raise ProblemError(
            code=ErrorCode.IDENTITY_INVALID,
            title="Not forwarded by Primer",
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Identity is accepted only on requests the web app forwarded.",
        )

    subject = (request.headers.get(settings.subject_header) or "").strip()
    if not subject:
        raise ProblemError(
            code=ErrorCode.IDENTITY_MISSING,
            title="Identity missing",
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="The authenticating proxy did not supply a trusted subject.",
        )
    return Principal(
        subject=subject,
        user_id=user_id_for_subject(subject),
        email=(request.headers.get(settings.email_header) or "").strip() or None,
        groups=_groups_from(request.headers.get(settings.groups_header), settings.groups_delimiter),
    )


def _groups_from(raw: str | None, delimiter: str) -> tuple[str, ...]:
    """The groups the proxy asserted, which is the whole of the admin test."""
    if not raw:
        return ()
    return tuple(group.strip() for group in raw.split(delimiter) if group.strip())


def ensure_admin(principal: Principal, settings: IdentitySettings, *, restricted: str) -> Principal:
    """Refuse anyone who may not see or change how this deployment is wired.

    403 rather than the 404 used for libraries. Absence and denial are worth
    conflating for a resource whose existence is itself private; a
    deployment's settings are not private in that way, and telling an
    ordinary user "not for you" is more useful than pretending they do not
    exist. `restricted` says what is restricted, as the refusal's subject.
    """
    if not is_admin(
        principal, auth_enabled=settings.auth_enabled, admin_group=settings.admin_group
    ):
        raise ProblemError(
            code=ErrorCode.IDENTITY_INVALID,
            title="Not an administrator",
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"{restricted} is restricted to administrators.",
        )
    return principal
