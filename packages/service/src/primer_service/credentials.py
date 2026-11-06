"""The credentials one Primer service presents to another.

Each caller holds its own: the web app, Chat, and the ingestion workers are
trusted for different things, and a shared token would let any of them do
what the others may. A route names the setting holding the one credential it
accepts, so which callers reach which routes is written where the route is.
"""

from __future__ import annotations

from collections.abc import Callable
from hmac import compare_digest

from fastapi import Request, status
from primer_contracts.errors import ErrorCode
from pydantic import SecretStr

from primer_service.errors import ProblemError

SERVICE_TOKEN_HEADER = "X-Primer-Service-Token"  # noqa: S105 - a header name, not a secret
WEB_TOKEN_HEADER = "X-Primer-Web-Token"  # noqa: S105 - a header name, not a secret


def presents(request: Request, header: str, configured: SecretStr | None) -> bool:
    """Whether the request carries the configured credential in `header`.

    An unset credential matches nothing. Treating "none configured" as "none
    required" would turn a missing environment variable into an open API,
    which is exactly the failure that must never be silent. Compared as bytes
    and always compared, so neither a non-ASCII header nor an early return
    leaks anything about the expected value.
    """
    presented = request.headers.get(header, "")
    expected = configured.get_secret_value().encode() if configured is not None else b""
    return compare_digest(presented.encode(), expected) and bool(expected)


def require_credential(setting: str) -> Callable[[Request], None]:
    """A dependency admitting only callers that hold the credential in `setting`.

    `setting` names a `SecretStr | None` field on the app's settings, read
    per request so a test can build an app with whatever settings it needs.
    """

    def check(request: Request) -> None:
        configured: SecretStr | None = getattr(request.app.state.settings, setting)
        if not presents(request, SERVICE_TOKEN_HEADER, configured):
            raise ProblemError(
                code=ErrorCode.IDENTITY_INVALID,
                title="Service credential required",
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="This endpoint is reachable only by the Primer service that uses it.",
            )

    return check
