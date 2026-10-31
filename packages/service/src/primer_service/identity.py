"""Turn the headers the authenticating proxy set into a `Principal`.

Every service that answers a user derives the same identity from the same
subject, so a library created through Control, a conversation in Chat, and
a run in Lab all belong to the same person. Control and Chat each held a
copy of this, identical down to the namespace UUID; Lab would have been the
third, and a third copy is the one that ends up with a different namespace
and gives the same person two identities.

What stays in each service is what it guards with this: which routes need
an administrator, and how it words a refusal.
"""

from __future__ import annotations

import uuid
from typing import Protocol

from fastapi import Request, status
from primer_contracts.errors import ErrorCode
from primer_contracts.identity import Principal

from primer_service.errors import ProblemError

#: Namespace for deriving stable user UUIDs from OIDC subjects. Changing it
#: changes every user's id, and with it who owns every library.
PRIMER_USER_NAMESPACE = uuid.UUID("6f2f5a10-9f4b-5f0e-9b53-7a1a5c6d8e10")

LOCAL_SUBJECT = "primer-local-user"


class IdentitySettings(Protocol):
    """The settings every service reads identity with, under the same names."""

    @property
    def auth_mode(self) -> str: ...
    @property
    def subject_header(self) -> str: ...
    @property
    def email_header(self) -> str: ...
    @property
    def groups_header(self) -> str: ...
    @property
    def groups_delimiter(self) -> str: ...


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
