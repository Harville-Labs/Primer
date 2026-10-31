"""Lab's status, and choosing its model."""

from __future__ import annotations

from fastapi import APIRouter, Request, status
from primer_contracts.errors import ErrorCode
from primer_contracts.lab import LabStatus, LoadModelRequest
from primer_service.errors import ProblemError

from primer_lab.config import Settings
from primer_lab.host import ModelHost, SwitchInProgress
from primer_lab.identity import CurrentPrincipal, can_choose_model
from primer_lab.queue import RunQueue
from primer_lab.sources import ModelUnavailable

router = APIRouter(prefix="/api/v1/lab", tags=["lab"])


def _status(request: Request, allowed: bool) -> LabStatus:
    settings: Settings = request.app.state.settings
    host: ModelHost = request.app.state.host
    queue: RunQueue = request.app.state.queue
    return LabStatus(
        ready=host.resident is not None,
        model=host.resident.status() if host.resident else None,
        switch=host.switch,
        can_choose_model=allowed,
        max_model_bytes=settings.lab_max_model_bytes,
        queue_depth=queue.depth,
        max_prompt_tokens=settings.lab_max_prompt_tokens,
        max_new_tokens=settings.lab_max_new_tokens,
    )


@router.get("/status", summary="The resident model and any switch in progress")
async def lab_status(request: Request, principal: CurrentPrincipal) -> LabStatus:
    return _status(request, can_choose_model(request.app.state.settings, principal))


@router.put(
    "/model",
    summary="Switch the resident model",
    status_code=status.HTTP_202_ACCEPTED,
    description=(
        "Starts fetching and loading the named model, and answers once that has started. "
        "The switch replaces the model for every user of this Lab; poll the status to see "
        "it finish. Naming the model already resident, or already being switched to, "
        "changes nothing."
    ),
)
async def choose_model(
    request: Request, body: LoadModelRequest, principal: CurrentPrincipal
) -> LabStatus:
    settings: Settings = request.app.state.settings
    if not can_choose_model(settings, principal):
        raise ProblemError(
            code=ErrorCode.IDENTITY_INVALID,
            title="Not allowed to change the model",
            status_code=status.HTTP_403_FORBIDDEN,
            detail="On this deployment the model Lab loads is chosen by "
            + ("administrators." if settings.lab_model_choice == "admins" else "the operator."),
        )
    host: ModelHost = request.app.state.host
    try:
        host.request(body.model)
    except SwitchInProgress:
        raise ProblemError(
            code=ErrorCode.CONFLICT,
            title="Another model is being loaded",
            status_code=status.HTTP_409_CONFLICT,
            detail="Wait for the switch in progress to finish, then try again.",
        ) from None
    except ModelUnavailable as error:
        raise ProblemError(
            code=ErrorCode.VALIDATION_FAILED,
            title="That model cannot be used",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from None
    return _status(request, True)
