"""RFC 9457 problem documents, in the one shape Primer promises.

Three services answer HTTP with these, and they had three copies of it that
had already drifted - in whitespace and docstrings mostly, but also in where
the request id came from, which is the sort of difference that is invisible
until someone tries to correlate a report with a log.

The service-specific factories (`identity_missing`, `not_found`,
`dependency_unavailable`) stay in the services. What a service calls its own
failures is not shared vocabulary; the envelope they travel in is.

Not extracted alongside this: the service-credential check. Control and
Retrieval both hold one and the copies are deliberate - the two services
hold different credentials and neither should be able to present the other's,
which a shared import quietly makes possible. The rule written there is to
extract when a third service needs it, and a third one still does not.
"""

from __future__ import annotations

from fastapi import Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from primer_contracts.errors import ErrorCode, ProblemDetail

PROBLEM_MEDIA_TYPE = "application/problem+json"


class ProblemError(Exception):
    """An error that renders as a `ProblemDetail` body.

    Messages here are user-facing and must stay sanitized; operational
    context belongs in logs, correlated by request ID.
    """

    def __init__(
        self, code: ErrorCode, title: str, status_code: int, detail: str | None = None
    ) -> None:
        super().__init__(title)
        self.code = code
        self.title = title
        self.status_code = status_code
        self.detail = detail


def request_id_of(request: Request) -> str | None:
    """The id this failure should be correlated by.

    State first, then the header, because the two services differed on which
    they read. Control runs a middleware that mints an id when the caller
    sends none and puts it on the request state; Chat and Retrieval have no
    such middleware and only ever had the header to read.

    So this is the union rather than a fix: it is correct for all three
    today, and it is already correct for the other two on the day they gain
    the middleware. Until then a request that reaches them without an
    `X-Request-ID` still gets a null id, which is worth doing something about
    separately - the middleware as written subclasses `BaseHTTPMiddleware`,
    and that buffers responses, which Chat cannot have: it streams.
    """
    generated = getattr(request.state, "request_id", None)
    if isinstance(generated, str) and generated:
        return generated
    return request.headers.get("X-Request-ID")


#: Where a rejected field sits in the request, as FastAPI reports it. The
#: first element says which part - body, query, path - and is not worth
#: repeating back to someone who can see the form they just submitted.
def _field_of(location: tuple[object, ...]) -> str:
    parts = [str(part) for part in location[1:]] or [str(part) for part in location]
    return ".".join(parts)


def validation_problem(error: RequestValidationError) -> ProblemDetail:
    """Turn FastAPI's validation errors into the error shape Primer promises.

    Every other failure here is an RFC 9457 problem document, but a request
    that fails validation is caught by the framework before any of Primer's
    code runs, and FastAPI answers in its own shape: a list of objects under
    `detail`. Clients written against the contract read `detail` as a string,
    so the web app rendered a rejected field name as "[object Object]" - a
    message that tells a user nothing and reads as a broken application.

    The messages are pydantic's, which are written for people ("String should
    have at most 120 characters"), and are named with the field they concern.
    The rejected input itself is deliberately not echoed: it is already on the
    user's screen, and it is the one part of this that could be anything.
    """
    described = "; ".join(
        f"{_field_of(tuple(fault.get('loc', ())))}: {fault.get('msg', 'is not valid')}"
        for fault in error.errors()
    )
    return ProblemDetail(
        code=ErrorCode.VALIDATION_FAILED,
        title="Request could not be accepted",
        status=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=described or "The request was not in the expected shape.",
    )


def problem_of(request: Request, error: ProblemError) -> ProblemDetail:
    """The problem document for `error`, for a response or a stream's last event."""
    return ProblemDetail(
        code=error.code,
        title=error.title,
        status=error.status_code,
        detail=error.detail,
        request_id=request_id_of(request),
    )


def problem_response(request: Request, error: ProblemError) -> JSONResponse:
    return rendered(request, problem_of(request, error))


def rendered(request: Request, problem: ProblemDetail) -> JSONResponse:
    """One place that decides what a problem looks like on the wire."""
    return JSONResponse(
        status_code=problem.status,
        content=problem.model_copy(update={"request_id": request_id_of(request)}).model_dump(
            mode="json"
        ),
        media_type=PROBLEM_MEDIA_TYPE,
    )
