"""The stage protocol, against a real database.

The worker tests prove how a worker reacts to each outcome. These prove the
outcomes themselves: that concurrent claims, redelivered completions, and
superseded generations resolve the way the protocol promises.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
import pytest_asyncio
from control_credentials import SERVICE_TOKEN
from control_support import ServiceClient, UserClient
from httpx2 import AsyncClient
from primer_control.models import IngestionJob
from primer_service.db import Database
from sqlalchemy import select, text


@dataclass(frozen=True)
class QueuedJob:
    job_id: str
    generation_id: str
    document_id: str


@pytest_asyncio.fixture
async def job(owner: UserClient, library_id: str, database: Database) -> QueuedJob:
    """A real job, created the only way one can be: by uploading a document."""
    document = (await owner.upload(library_id, "paper.txt", b"evidence")).json()
    async with database.session() as session:
        row = (await session.execute(select(IngestionJob))).scalar_one()
        return QueuedJob(str(row.id), str(row.generation_id), document["id"])


async def expire_lease(database: Database, job_id: str) -> None:
    """Simulate a worker that died without releasing its claim."""
    async with database.engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE control.ingestion_jobs "
                "SET lease_expires_at = now() - interval '1 hour' WHERE id = :id"
            ),
            {"id": job_id},
        )


async def test_a_claim_returns_the_scope_the_work_needs(
    service: ServiceClient, job: QueuedJob, library_id: str
) -> None:
    """Without library and generation, a worker could not produce scoped chunks."""
    body = (await service.claim(job.job_id)).json()

    assert body["outcome"] == "claimed"
    claim = body["claim"]
    assert claim["stage"] == "parse"
    assert claim["library_id"] == library_id
    assert claim["document_id"] == job.document_id
    assert claim["generation_id"] == job.generation_id
    assert claim["attempt"] == 1
    assert claim["source_sha256"] and claim["filename"] == "paper.txt"


async def test_a_second_claim_against_a_live_lease_is_refused(
    service: ServiceClient, job: QueuedJob
) -> None:
    """Two workers handed the same message: only one may proceed."""
    assert (await service.claim(job.job_id)).json()["outcome"] == "claimed"
    second = (await service.claim(job.job_id)).json()

    assert second["outcome"] == "in_progress"
    assert second["claim"] is None


async def test_a_claim_after_completion_is_already_completed(
    service: ServiceClient, job: QueuedJob
) -> None:
    await service.claim(job.job_id)
    await service.complete(job.job_id, job.generation_id)

    assert (await service.claim(job.job_id)).json()["outcome"] == "already_completed"


async def test_an_expired_lease_is_reclaimable(
    service: ServiceClient, job: QueuedJob, database: Database
) -> None:
    """A crashed worker must not strand a job forever."""
    await service.claim(job.job_id)
    await expire_lease(database, job.job_id)

    retaken = (await service.claim(job.job_id)).json()

    assert retaken["outcome"] == "claimed"
    assert retaken["claim"]["attempt"] == 2


async def test_a_heartbeat_holds_the_claim_against_another_worker(
    service: ServiceClient, job: QueuedJob, database: Database
) -> None:
    await service.claim(job.job_id)
    await expire_lease(database, job.job_id)

    assert (await service.heartbeat(job.job_id, job.generation_id)).json()["applied"] is True
    assert (await service.claim(job.job_id)).json()["outcome"] == "in_progress"


async def test_completion_applies_once(service: ServiceClient, job: QueuedJob) -> None:
    await service.claim(job.job_id)

    first = (await service.complete(job.job_id, job.generation_id)).json()
    second = (await service.complete(job.job_id, job.generation_id)).json()

    assert first == {"applied": True, "status": "chunking"}
    assert second == {"applied": False, "status": "chunking"}


async def test_a_superseded_generation_cannot_complete(
    service: ServiceClient, job: QueuedJob
) -> None:
    """A slow worker must not finish work a reindex has already replaced."""
    await service.claim(job.job_id)
    stale = "00000000-0000-5000-8000-000000000000"

    result = (await service.complete(job.job_id, stale)).json()

    assert result == {"applied": False, "status": "parsing"}


async def test_the_pipeline_walks_parse_to_ready(service: ServiceClient, job: QueuedJob) -> None:
    """Each stage's done state is the next stage's entry state."""
    for stage, expected in [
        ("parse", "chunking"),
        ("embed", "indexing"),
        ("index", "ready"),
    ]:
        assert (await service.claim(job.job_id, stage)).json()["outcome"] == "claimed"
        result = (await service.complete(job.job_id, job.generation_id, stage)).json()
        assert result == {"applied": True, "status": expected}


async def test_a_retryable_failure_returns_the_job_to_its_entry_state(
    service: ServiceClient, job: QueuedJob
) -> None:
    await service.claim(job.job_id)

    result = (await service.fail(job.job_id, job.generation_id, code="timeout")).json()

    assert result == {"applied": True, "status": "queued"}
    assert (await service.claim(job.job_id)).json()["outcome"] == "claimed"


async def test_the_attempt_bound_is_enforced_by_control(
    service: ServiceClient, job: QueuedJob
) -> None:
    """A broker redelivery resets a worker's counter; this one it cannot."""
    await service.claim(job.job_id)
    await service.fail(job.job_id, job.generation_id, code="timeout")

    await service.claim(job.job_id)
    exhausted = (await service.fail(job.job_id, job.generation_id, code="timeout")).json()

    assert exhausted == {"applied": True, "status": "failed"}
    assert (await service.claim(job.job_id)).json()["outcome"] == "already_completed"


async def test_an_unsupported_document_fails_without_spending_retries(
    service: ServiceClient, job: QueuedJob
) -> None:
    await service.claim(job.job_id)

    result = (
        await service.fail(
            job.job_id,
            job.generation_id,
            code="ocr_required",
            detail="This PDF holds no extractable text.",
            disposition="unsupported",
        )
    ).json()

    assert result == {"applied": True, "status": "unsupported"}


async def test_job_state_is_what_the_document_reports(
    service: ServiceClient, owner: UserClient, job: QueuedJob, library_id: str
) -> None:
    """One vocabulary: the worker's transitions are the user's status."""
    path = f"/api/v1/libraries/{library_id}/documents/{job.document_id}"
    assert (await owner.get(path)).json()["status"] == "queued"

    await service.claim(job.job_id)
    assert (await owner.get(path)).json()["status"] == "parsing"

    await service.fail(
        job.job_id, job.generation_id, code="ocr_required", disposition="unsupported"
    )
    summary = (await owner.get(path)).json()
    assert summary["status"] == "unsupported"


@pytest.mark.parametrize("token", [None, "wrong-token", SERVICE_TOKEN])
async def test_the_job_api_admits_only_the_worker_credential(
    client: AsyncClient, job: QueuedJob, token: str | None
) -> None:
    """An unset or wrong credential is refused; there is no anonymous path in.

    Chat's credential is wrong here too. Chat asks what a person may read; a
    credential that could also finish someone's ingestion job would let a
    compromised Chat rewrite what a library holds.
    """
    intruder = ServiceClient(client, token)

    response = await intruder.claim(job.job_id)

    assert response.status_code == 401
    assert response.json()["code"] == "identity_invalid"


async def test_a_user_identity_does_not_open_the_internal_api(
    owner: UserClient, job: QueuedJob
) -> None:
    """Edge identity headers are not a service credential."""
    response = await owner.post(
        f"/internal/v1/ingestion/jobs/{job.job_id}/claim", {"stage": "parse"}
    )
    assert response.status_code == 401


async def test_an_unknown_job_is_not_found(service: ServiceClient) -> None:
    missing = "00000000-0000-5000-8000-000000000000"
    response = await service.claim(missing)

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


async def test_finishing_the_index_stage_activates_the_generation(
    service: ServiceClient, owner: UserClient, job: QueuedJob, library_id: str
) -> None:
    """Ready and activated are one event, so neither can be observed alone."""
    generations = f"/internal/v1/ingestion/libraries/{library_id}/generations"
    assert (await service.get(generations)).json() == []

    for stage in ("parse", "embed", "index"):
        await service.claim(job.job_id, stage)
        await service.complete(job.job_id, job.generation_id, stage)

    path = f"/api/v1/libraries/{library_id}/documents/{job.document_id}"
    assert (await owner.get(path)).json()["status"] == "ready"
    assert (await service.get(generations)).json() == [job.generation_id]


async def test_an_unfinished_index_answers_for_nothing(
    service: ServiceClient, job: QueuedJob, library_id: str
) -> None:
    """A document still being indexed must not be searchable."""
    await service.claim(job.job_id)
    await service.complete(job.job_id, job.generation_id)

    generations = f"/internal/v1/ingestion/libraries/{library_id}/generations"
    assert (await service.get(generations)).json() == []


async def test_a_deleted_document_stops_answering_before_its_vectors_go(
    service: ServiceClient, owner: UserClient, job: QueuedJob, library_id: str
) -> None:
    """The tombstone is what takes it out of reach, not the cleanup."""
    for stage in ("parse", "embed", "index"):
        await service.claim(job.job_id, stage)
        await service.complete(job.job_id, job.generation_id, stage)

    await owner.delete(f"/api/v1/libraries/{library_id}/documents/{job.document_id}")

    generations = f"/internal/v1/ingestion/libraries/{library_id}/generations"
    assert (await service.get(generations)).json() == []
