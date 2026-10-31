"""The embed and index stages, against a recording Retrieval."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from pathlib import Path

import pytest
from primer_contracts.chunks import DocumentChunk
from primer_contracts.graph import ChunkGraph, GraphEntity
from primer_contracts.indexing import (
    DeleteRequest,
    DeleteResult,
    GenerationCount,
    GenerationQuery,
    GraphIndexRequest,
    GraphIndexResult,
    IndexRequest,
    IndexResult,
    PurgeRequest,
)
from primer_contracts.ingestion import JobClaim, StageName
from primer_ingestion.config import Settings
from primer_ingestion.errors import PermanentStageError, StageError
from primer_ingestion.stages.delete import DeleteStage
from primer_ingestion.stages.embed import EmbedStage
from primer_ingestion.stages.index import IndexStage
from primer_ingestion.stages.parse import CHUNKS_ARTIFACT
from primer_storage import ArtifactStore

VERSION = uuid.uuid5(uuid.NAMESPACE_URL, "version")
GENERATION = uuid.uuid5(uuid.NAMESPACE_URL, "generation")
LIBRARY = uuid.uuid5(uuid.NAMESPACE_URL, "library")


class RecordingIndex:
    """A Retrieval that records what it was asked to store."""

    def __init__(
        self, reported_count: int | None = None, reported_graph_count: int | None = None
    ) -> None:
        self.requests: list[IndexRequest] = []
        self.graph_requests: list[GraphIndexRequest] = []
        self.purges: list[PurgeRequest] = []
        self.reported_count = reported_count
        self.reported_graph_count = reported_graph_count

    @property
    def stored(self) -> int:
        return sum(len(request.chunks) for request in self.requests)

    def index(self, request: IndexRequest) -> IndexResult:
        self.requests.append(request)
        return IndexResult(generation_id=request.generation_id, written=len(request.chunks))

    def index_graph(self, request: GraphIndexRequest) -> GraphIndexResult:
        self.graph_requests.append(request)
        return GraphIndexResult(generation_id=request.generation_id, written=len(request.graphs))

    def verify(self, request: GenerationQuery) -> GenerationCount:
        count = self.reported_count if self.reported_count is not None else self.stored
        return GenerationCount(
            generation_id=request.generation_id,
            count=count,
            graph_count=self.reported_graph_count,
        )

    def delete(self, request: DeleteRequest) -> DeleteResult:
        return DeleteResult(generation_id=request.generation_id, deleted=0)

    def purge(self, request: PurgeRequest) -> DeleteResult:
        self.purges.append(request)
        return DeleteResult(
            generation_id=request.keep_generation_id or request.document_version_id, deleted=0
        )


class RecordingExtractor:
    """A model that names one entity per passage, and records what it read."""

    def __init__(self) -> None:
        self.batches: list[list[DocumentChunk]] = []

    def extract(self, chunks: Sequence[DocumentChunk]) -> list[ChunkGraph]:
        self.batches.append(list(chunks))
        return [
            ChunkGraph(chunk_id=chunk.chunk_id, entities=(GraphEntity(name=chunk.content),))
            for chunk in chunks
        ]


def chunk_payload(ordinal: int) -> dict[str, object]:
    return {
        "chunk_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{VERSION}:{GENERATION}:{ordinal}")),
        "ordinal": ordinal,
        "library_id": str(LIBRARY),
        "document_id": str(uuid.uuid5(uuid.NAMESPACE_URL, "document")),
        "document_version_id": str(VERSION),
        "owner_user_id": str(uuid.uuid5(uuid.NAMESPACE_URL, "owner")),
        "generation_id": str(GENERATION),
        "content": f"passage {ordinal}",
        "embedding_text": f"Findings\npassage {ordinal}",
        "locator": {"page": 1, "section": "Findings"},
        "filename": "paper.pdf",
    }


@pytest.fixture
def claim() -> JobClaim:
    return JobClaim(
        job_id=uuid.uuid5(uuid.NAMESPACE_URL, "job"),
        stage=StageName.EMBED,
        generation_id=GENERATION,
        attempt=1,
        document_id=uuid.uuid5(uuid.NAMESPACE_URL, "document"),
        document_version_id=VERSION,
        library_id=LIBRARY,
        owner_user_id=uuid.uuid5(uuid.NAMESPACE_URL, "owner"),
        source_sha256="c" * 64,
        filename="paper.pdf",
        media_type="application/pdf",
        byte_size=2048,
    )


@pytest.fixture
def artifacts(tmp_path: Path) -> ArtifactStore:
    return ArtifactStore(f"file://{tmp_path}")


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(source_store_url=f"file://{tmp_path}", index_batch_size=2)


def write_chunks(artifacts: ArtifactStore, count: int) -> None:
    artifacts.write_json(
        VERSION, GENERATION, CHUNKS_ARTIFACT, [chunk_payload(n) for n in range(count)]
    )


def test_every_chunk_reaches_retrieval_with_its_scope(
    settings: Settings, artifacts: ArtifactStore, claim: JobClaim
) -> None:
    write_chunks(artifacts, 5)
    index = RecordingIndex()

    EmbedStage(settings, artifacts=artifacts, index=index)(claim)

    assert index.stored == 5
    assert all(request.library_id == LIBRARY for request in index.requests)
    assert all(request.generation_id == GENERATION for request in index.requests)


def test_chunks_are_sent_in_bounded_batches(
    settings: Settings, artifacts: ArtifactStore, claim: JobClaim
) -> None:
    """One timeout should cost a batch, not a whole document."""
    write_chunks(artifacts, 5)
    index = RecordingIndex()

    EmbedStage(settings, artifacts=artifacts, index=index)(claim)

    assert sorted(len(request.chunks) for request in index.requests) == [1, 2, 2]


def test_embedding_without_parsed_chunks_fails_permanently(
    settings: Settings, artifacts: ArtifactStore, claim: JobClaim
) -> None:
    """Parse said it finished; if its output is gone, retrying cannot restore it."""
    with pytest.raises(PermanentStageError) as raised:
        EmbedStage(settings, artifacts=artifacts, index=RecordingIndex())(claim)
    assert raised.value.code == "chunks_missing"


def test_a_complete_generation_passes_verification(
    settings: Settings, artifacts: ArtifactStore, claim: JobClaim
) -> None:
    write_chunks(artifacts, 3)
    index = RecordingIndex(reported_count=3)

    IndexStage(settings, artifacts=artifacts, index=index)(claim)


def test_a_short_generation_is_refused_and_retried(
    settings: Settings, artifacts: ArtifactStore, claim: JobClaim
) -> None:
    """Activating a short index would drop passages from every future answer."""
    write_chunks(artifacts, 3)
    index = RecordingIndex(reported_count=2)

    with pytest.raises(StageError) as raised:
        IndexStage(settings, artifacts=artifacts, index=index)(claim)

    assert raised.value.code == "incomplete_index"
    assert type(raised.value) is StageError


def test_without_an_extraction_model_no_graph_is_written(
    settings: Settings, artifacts: ArtifactStore, claim: JobClaim
) -> None:
    """Extraction costs a model call per passage, so it is never on by default."""
    write_chunks(artifacts, 3)
    index = RecordingIndex()

    EmbedStage(settings, artifacts=artifacts, index=index)(claim)

    assert index.stored == 3
    assert index.graph_requests == []


def test_each_batch_is_extracted_into_the_same_pending_generation(
    settings: Settings, artifacts: ArtifactStore, claim: JobClaim
) -> None:
    """The graph and the vectors are activated together, so they share a generation."""
    write_chunks(artifacts, 3)
    index, extractor = RecordingIndex(), RecordingExtractor()

    EmbedStage(settings, artifacts=artifacts, index=index, extractor=extractor)(claim)

    assert sorted(len(batch) for batch in extractor.batches) == [1, 2]
    assert sorted(len(request.graphs) for request in index.graph_requests) == [1, 2]
    assert {request.generation_id for request in index.graph_requests} == {GENERATION}
    assert {request.library_id for request in index.graph_requests} == {LIBRARY}


def extraction_on(settings: Settings) -> Settings:
    return settings.model_copy(
        update={"graph_extraction_base_url": "http://model:8000/v1", "graph_extraction_model": "m"}
    )


def test_a_complete_graph_passes_verification(
    settings: Settings, artifacts: ArtifactStore, claim: JobClaim
) -> None:
    write_chunks(artifacts, 3)
    index = RecordingIndex(reported_count=3, reported_graph_count=3)

    IndexStage(extraction_on(settings), artifacts=artifacts, index=index)(claim)


def test_a_short_graph_is_refused_and_retried(
    settings: Settings, artifacts: ArtifactStore, claim: JobClaim
) -> None:
    """Complete vectors are not enough: a partial graph answers from part of the document."""
    write_chunks(artifacts, 3)
    index = RecordingIndex(reported_count=3, reported_graph_count=2)

    with pytest.raises(StageError) as raised:
        IndexStage(extraction_on(settings), artifacts=artifacts, index=index)(claim)

    assert raised.value.code == "incomplete_graph"
    assert type(raised.value) is StageError


def test_extraction_against_a_retrieval_with_no_graph_fails_permanently(
    settings: Settings, artifacts: ArtifactStore, claim: JobClaim
) -> None:
    """Two services configured differently disagree on every retry."""
    write_chunks(artifacts, 3)
    index = RecordingIndex(reported_count=3, reported_graph_count=None)

    with pytest.raises(PermanentStageError) as raised:
        IndexStage(extraction_on(settings), artifacts=artifacts, index=index)(claim)

    assert raised.value.code == "graph_not_configured"


def test_the_worker_registers_a_handler_for_every_pipeline_stage() -> None:
    """A missing handler would fail documents for a deployment reason."""
    from primer_ingestion.tasks import HANDLERS
    from primer_ingestion.worker import register_stages

    register_stages(Settings())

    assert {StageName.PARSE, StageName.EMBED, StageName.INDEX} <= set(HANDLERS)


class RecordingControl:
    """Control, recording what cleanup asked it to drop."""

    def __init__(self, freed: list[str] | None = None) -> None:
        self.freed = freed if freed is not None else []
        self.purge_calls = 0

    def purge(self, job_id: uuid.UUID) -> list[str]:
        self.purge_calls += 1
        return self.freed


class RecordingSources:
    def __init__(self) -> None:
        self.removed: list[str] = []

    def remove(self, sha256: str) -> None:
        self.removed.append(sha256)


def delete_claim(claim: JobClaim) -> JobClaim:
    return claim.model_copy(update={"stage": StageName.DELETE})


def test_cleanup_removes_passages_then_rows_then_bytes(
    settings: Settings, artifacts: ArtifactStore, claim: JobClaim
) -> None:
    """Bytes go last, and only once the database says nothing points at them."""
    index = RecordingIndex()
    control = RecordingControl(freed=["a" * 64])
    sources = RecordingSources()

    DeleteStage(settings, control=control, index=index, sources=sources, artifacts=artifacts)(
        delete_claim(claim)
    )

    assert control.purge_calls == 1
    assert sources.removed == ["a" * 64]
    # Every generation, not just the current one.
    assert index.purges[0].keep_generation_id is None
    assert index.purges[0].document_version_id == claim.document_version_id


def test_cleanup_leaves_bytes_another_document_still_uses(
    settings: Settings, artifacts: ArtifactStore, claim: JobClaim
) -> None:
    """Deduplication must not let one delete erase another user's document."""
    sources = RecordingSources()

    DeleteStage(
        settings,
        control=RecordingControl(freed=[]),
        index=RecordingIndex(),
        sources=sources,
        artifacts=artifacts,
    )(delete_claim(claim))

    assert sources.removed == []


def test_retiring_superseded_generations_spares_the_live_one(
    settings: Settings, artifacts: ArtifactStore, claim: JobClaim
) -> None:
    """Purging without sparing the active build would empty a live index."""
    index = RecordingIndex()

    IndexStage(settings, artifacts=artifacts, index=index).retire_superseded(claim)

    assert index.purges[0].keep_generation_id == claim.generation_id


def test_superseded_generations_are_retired_only_after_the_switch(
    settings: Settings, artifacts: ArtifactStore, claim: JobClaim
) -> None:
    """Until Control has switched, the old generation is what searches read."""
    from primer_ingestion.tasks import _after_activation

    index = RecordingIndex(reported_count=1)
    stage = IndexStage(settings, artifacts=artifacts, index=index)

    write_chunks(artifacts, 1)
    stage(claim.model_copy(update={"stage": StageName.INDEX}))
    assert index.purges == []

    _after_activation(stage, claim)
    assert len(index.purges) == 1


def test_a_failed_retirement_does_not_fail_a_finished_job(
    settings: Settings, artifacts: ArtifactStore, claim: JobClaim
) -> None:
    """The generation is already live; leftover storage is not a wrong answer."""
    from primer_ingestion.tasks import _after_activation

    class Broken(IndexStage):
        def retire_superseded(self, claim: JobClaim) -> None:
            raise RuntimeError("retrieval unreachable")

    _after_activation(Broken(settings, artifacts=artifacts, index=RecordingIndex()), claim)
