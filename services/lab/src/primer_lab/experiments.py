"""Saved experiments: a run a user chose to keep, and what it needs to be repeated.

Nothing is stored until someone saves. Generating, scoring, and training
keep nothing past the model's lifetime; saving is the one act that writes,
and it writes what the caller sent back along with the steering vectors and
probes that run used. Those are kept as their numbers, so restoring one puts
the same vector back under the same id, and the saved run's request works
again unchanged.

A vector or a probe means something only in the residual stream of the model
it was made on, so an experiment records that model and is restored only
onto the same one at the same commit.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

import numpy as np
import torch
from fastapi import APIRouter, Depends, Request, Response, status
from primer_contracts.errors import ErrorCode
from primer_contracts.lab import (
    GenerateRequest,
    GenerationCompleted,
    HubModel,
    LabModelStatus,
    Probe,
    SteeringVector,
)
from primer_service.db import get_session
from primer_service.durable import DurableRoute
from primer_service.errors import ProblemError
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from primer_lab import saved
from primer_lab.config import Settings
from primer_lab.host import Resident
from primer_lab.identity import CurrentPrincipal
from primer_lab.probes import Fitted
from primer_lab.routes import current_resident, refused
from primer_lab.scoring import LabScored, LabScoreRequest
from primer_lab.tables import ExperimentRow

#: Per owner. Each holds its vectors' and probes' numbers, a few hundred
#: kilobytes at most, so this bounds what one person can keep.
MAX_PER_OWNER = 200

_STRICT = ConfigDict(extra="forbid", frozen=True)


class NeuronsRun(BaseModel):
    """A generation with edits, from the Neurons tab."""

    model_config = _STRICT

    kind: Literal["neurons"] = "neurons"
    request: GenerateRequest
    #: None when saved before it was run.
    result: GenerationCompleted | None = None


class AnomaliesRun(BaseModel):
    """A score with Lab's detectors, from the Anomalies tab."""

    model_config = _STRICT

    kind: Literal["anomalies"] = "anomalies"
    request: LabScoreRequest
    result: LabScored | None = None


Run = Annotated[NeuronsRun | AnomaliesRun, Field(discriminator="kind")]
_RUN: TypeAdapter[NeuronsRun | AnomaliesRun] = TypeAdapter(Run)

Title = Annotated[str, Field(min_length=1, max_length=200)]


class ExperimentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    title: Title
    run: Run


class ExperimentRename(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    title: Title


class ExperimentSummary(BaseModel):
    model_config = _STRICT

    id: UUID
    kind: Literal["neurons", "anomalies"]
    title: str
    #: The model it was run on, which is the only one it can be restored onto.
    model: LabModelStatus
    created_at: datetime
    #: When the retention period ends and it is deleted.
    expires_at: datetime


class Experiment(ExperimentSummary):
    run: Run
    #: What was saved of each, without the numbers: those stay in Lab.
    vectors: list[SteeringVector]
    probes: list[Probe]


class _SavedVector(BaseModel):
    summary: SteeringVector
    values: list[float]


class _SavedProbe(BaseModel):
    summary: Probe
    weights: list[float]
    bias: float


def _unavailable(detail: str) -> ProblemError:
    return ProblemError(
        code=ErrorCode.DEPENDENCY_UNAVAILABLE,
        title="Experiments cannot be saved here",
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=detail,
    )


async def _session(request: Request) -> AsyncIterator[AsyncSession]:
    """The request's session, or a refusal that names the database as the cause.

    A deployment without a database still runs Lab; only saving is missing,
    and the status route says so before anyone tries. When the database is
    configured and down, the failure is a transport error that would
    otherwise reach the user as an unexplained 500.
    """
    if request.app.state.database is None:
        raise _unavailable(
            "This Lab has no database (PRIMER_DATABASE_URL), so it keeps nothing past the "
            "model's lifetime. An operator can configure one."
        )
    try:
        async with asynccontextmanager(get_session)(request) as session:
            yield session
    except (OSError, OperationalError, InterfaceError) as error:
        raise _unavailable(
            "Lab's database could not be reached. It may be restarting; try again shortly."
        ) from error


Session = Annotated[AsyncSession, Depends(_session)]

router = APIRouter(prefix="/api/v1/lab/experiments", tags=["lab"], route_class=DurableRoute)


def _clock(request: Request) -> tuple[datetime, int]:
    settings: Settings = request.app.state.settings
    return datetime.now(UTC), settings.lab_experiment_retention_days


def _since(request: Request) -> datetime:
    return saved.kept_since(*_clock(request))


def _summary(request: Request, row: ExperimentRow) -> ExperimentSummary:
    _, days = _clock(request)
    return ExperimentSummary(
        id=row.id,
        kind=row.kind,  # ty: ignore[invalid-argument-type]
        title=row.title,
        model=LabModelStatus.model_validate(row.model),
        created_at=row.created_at,
        expires_at=row.created_at + timedelta(days=days),
    )


def _experiment(request: Request, row: ExperimentRow) -> Experiment:
    return Experiment(
        **_summary(request, row).model_dump(),
        run=_RUN.validate_python(row.run),
        vectors=[_SavedVector.model_validate(v).summary for v in row.vectors],
        probes=[_SavedProbe.model_validate(p).summary for p in row.probes],
    )


def _not_found() -> ProblemError:
    return ProblemError(
        code=ErrorCode.NOT_FOUND,
        title="No such experiment",
        status_code=status.HTTP_404_NOT_FOUND,
        detail="You have no saved experiment with that id. It may have been deleted, or "
        "passed the retention period.",
    )


def _same_model(saved_on: LabModelStatus, resident: LabModelStatus) -> bool:
    return saved_on.reference == resident.reference and saved_on.commit == resident.commit


def _named(model: LabModelStatus) -> str:
    reference = model.reference
    name = reference.repository if isinstance(reference, HubModel) else reference.path
    return f"{name} at {model.commit[:12]}" if model.commit else name


def _used(resident: Resident, owner: UUID, run: NeuronsRun | AnomaliesRun) -> tuple[list, list]:
    """The caller's vectors and probes the run names, as they will be stored."""
    if isinstance(run, NeuronsRun):
        vector_ids = list(dict.fromkeys(i.vector_id for i in run.request.interventions))
        probe_ids: list[str | None] = []
    else:
        vector_ids, probe_ids = [], [run.request.probe_id]
    vectors, probes = [], []
    held_vectors = resident.vectors.owned_by(owner)
    summaries = {v.id: v for v in resident.vectors.listed(owner)}
    for vector_id in filter(None, vector_ids):
        if vector_id not in held_vectors:
            raise refused(
                "A steering vector it uses is gone",
                f"You have no steering vector {vector_id} on this model, so the run could "
                "not be repeated from what was saved. Make the vector again and rerun it.",
            )
        values = held_vectors[vector_id].float().tolist()
        vectors.append(_SavedVector(summary=summaries[vector_id], values=values))
    held_probes = resident.probes.owned_by(owner)
    probe_summaries = {p.id: p for p in resident.probes.listed(owner)}
    for probe_id in filter(None, probe_ids):
        if probe_id not in held_probes:
            raise refused(
                "The probe it uses is gone",
                f"You have no probe {probe_id} on this model. Train it again and rerun.",
            )
        fitted = held_probes[probe_id]
        probes.append(
            _SavedProbe(
                summary=probe_summaries[probe_id],
                weights=fitted.weights.tolist(),
                bias=fitted.bias,
            )
        )
    return vectors, probes


@router.post("", status_code=status.HTTP_201_CREATED, summary="Save an experiment")
async def save_experiment(
    request: Request, body: ExperimentCreate, principal: CurrentPrincipal, session: Session
) -> Experiment:
    resident = current_resident(request)
    model = resident.status()
    if body.run.result is not None and not _same_model(body.run.result.model, model):
        raise ProblemError(
            code=ErrorCode.CONFLICT,
            title="The model changed",
            status_code=status.HTTP_409_CONFLICT,
            detail="This run was made on a model Lab no longer holds, along with any vectors "
            "and probes it used. Run it again to save it.",
        )
    vectors, probes = _used(resident, principal.user_id, body.run)
    since = _since(request)
    if await saved.count(session, principal.user_id, since) >= MAX_PER_OWNER:
        raise ProblemError(
            code=ErrorCode.QUOTA_EXCEEDED,
            title="Too many saved experiments",
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"You have {MAX_PER_OWNER} saved, the most Lab keeps for one person. "
            "Delete one to save another.",
        )
    row = await saved.create(
        session,
        principal.user_id,
        kind=body.run.kind,
        title=body.title,
        model=model.model_dump(mode="json"),
        run=body.run.model_dump(mode="json"),
        vectors=[v.model_dump(mode="json") for v in vectors],
        probes=[p.model_dump(mode="json") for p in probes],
    )
    return _experiment(request, row)


@router.get("", summary="Your saved experiments, newest first")
async def list_experiments(
    request: Request, principal: CurrentPrincipal, session: Session
) -> list[ExperimentSummary]:
    rows = await saved.listed(session, principal.user_id, _since(request))
    return [_summary(request, row) for row in rows]


@router.get("/{experiment_id}", summary="One of your saved experiments")
async def get_experiment(
    request: Request, experiment_id: UUID, principal: CurrentPrincipal, session: Session
) -> Experiment:
    row = await saved.find(session, principal.user_id, experiment_id, _since(request))
    if row is None:
        raise _not_found()
    return _experiment(request, row)


@router.patch("/{experiment_id}", summary="Rename one of your saved experiments")
async def rename_experiment(
    request: Request,
    experiment_id: UUID,
    body: ExperimentRename,
    principal: CurrentPrincipal,
    session: Session,
) -> ExperimentSummary:
    row = await saved.find(session, principal.user_id, experiment_id, _since(request))
    if row is None:
        raise _not_found()
    row.title = body.title
    return _summary(request, row)


@router.delete(
    "/{experiment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete one of your saved experiments",
)
async def delete_experiment(
    request: Request, experiment_id: UUID, principal: CurrentPrincipal, session: Session
) -> Response:
    if not await saved.remove(session, principal.user_id, experiment_id, _since(request)):
        raise _not_found()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{experiment_id}/restore",
    summary="Put a saved experiment's vectors and probes back on the model",
    description=(
        "Holds the experiment's steering vectors and probes for you again, under the ids "
        "its run names, so the run can be repeated as it was saved. Only onto the model and "
        "commit it was saved on: a vector is a direction in one model's residual stream."
    ),
)
async def restore_experiment(
    request: Request, experiment_id: UUID, principal: CurrentPrincipal, session: Session
) -> Experiment:
    row = await saved.find(session, principal.user_id, experiment_id, _since(request))
    if row is None:
        raise _not_found()
    experiment = _experiment(request, row)
    resident = current_resident(request)
    if not _same_model(experiment.model, resident.status()):
        raise ProblemError(
            code=ErrorCode.CONFLICT,
            title="Saved on another model",
            status_code=status.HTTP_409_CONFLICT,
            detail=f"This experiment was saved on {_named(experiment.model)}. Its vectors "
            "and probes mean nothing on any other model; switch Lab to that one to restore it.",
        )
    for vector in (_SavedVector.model_validate(v) for v in row.vectors):
        resident.vectors.add(principal.user_id, vector.summary, torch.tensor(vector.values))
    for probe in (_SavedProbe.model_validate(p) for p in row.probes):
        fitted = Fitted(
            weights=np.array(probe.weights),
            bias=probe.bias,
            accuracy=probe.summary.accuracy,
            folds=probe.summary.folds,
            chance=probe.summary.chance,
            beats_chance=probe.summary.beats_chance,
        )
        resident.probes.add(principal.user_id, probe.summary, fitted)
    return experiment
