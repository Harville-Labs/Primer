"""The embed stage keeps several batches of one document in flight.

Embedding is a network round trip to a model server. Sent one at a time,
a document of a few thousand passages spends most of its time with the
server idle between a response leaving and the next request arriving, and
no number of workers helps the one document a user is waiting on.

The barriers below are what make these tests mean something: each only
opens once the calls it counts are genuinely waiting at the same moment, so
the sequential loop this replaced breaks them rather than passing slowly.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Sequence
from pathlib import Path

import pytest
from primer_contracts.chunks import DocumentChunk
from primer_contracts.graph import ChunkGraph
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
from primer_ingestion.errors import StageError
from primer_ingestion.stages.embed import EmbedStage
from primer_ingestion.stages.parse import CHUNKS_ARTIFACT
from primer_storage import ArtifactStore

VERSION = uuid.uuid5(uuid.NAMESPACE_URL, "version")
GENERATION = uuid.uuid5(uuid.NAMESPACE_URL, "generation")
LIBRARY = uuid.uuid5(uuid.NAMESPACE_URL, "library")
OWNER = uuid.uuid5(uuid.NAMESPACE_URL, "owner")
DOCUMENT = uuid.uuid5(uuid.NAMESPACE_URL, "document")

#: Long enough that a loaded CI machine does not trip it, short enough that
#: the sequential code fails these tests in seconds rather than hanging.
WAIT_SECONDS = 5


class ConcurrentIndex:
    """A Retrieval that records how many index calls overlap."""

    def __init__(self, barrier: threading.Barrier | None = None) -> None:
        self.barrier = barrier
        self.lock = threading.Lock()
        self.in_flight = 0
        self.peak = 0
        self.requests: list[IndexRequest] = []
        self.graph_requests: list[GraphIndexRequest] = []

    def index(self, request: IndexRequest) -> IndexResult:
        with self.lock:
            self.requests.append(request)
            self.in_flight += 1
            self.peak = max(self.peak, self.in_flight)
        try:
            if self.barrier is not None:
                self.barrier.wait()
        finally:
            with self.lock:
                self.in_flight -= 1
        return IndexResult(generation_id=request.generation_id, written=len(request.chunks))

    def index_graph(self, request: GraphIndexRequest) -> GraphIndexResult:
        with self.lock:
            self.graph_requests.append(request)
        return GraphIndexResult(generation_id=request.generation_id, written=len(request.graphs))

    # Embedding writes and nothing else; verifying and retiring belong to
    # the index stage, so either being called here is a bug.
    def verify(self, request: GenerationQuery) -> GenerationCount:
        raise AssertionError("the embed stage verified a generation")

    def delete(self, request: DeleteRequest) -> DeleteResult:
        raise AssertionError("the embed stage deleted a generation")

    def purge(self, request: PurgeRequest) -> DeleteResult:
        raise AssertionError("the embed stage purged a version")


def chunk_payload(ordinal: int) -> dict[str, object]:
    return {
        "chunk_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{VERSION}:{GENERATION}:{ordinal}")),
        "ordinal": ordinal,
        "library_id": str(LIBRARY),
        "document_id": str(DOCUMENT),
        "document_version_id": str(VERSION),
        "owner_user_id": str(OWNER),
        "generation_id": str(GENERATION),
        "content": f"passage {ordinal}",
        "embedding_text": f"passage {ordinal}",
        "locator": {"page": 1},
        "filename": "paper.pdf",
    }


@pytest.fixture
def claim() -> JobClaim:
    return JobClaim(
        job_id=uuid.uuid5(uuid.NAMESPACE_URL, "job"),
        stage=StageName.EMBED,
        generation_id=GENERATION,
        attempt=1,
        document_id=DOCUMENT,
        document_version_id=VERSION,
        library_id=LIBRARY,
        owner_user_id=OWNER,
        source_sha256="c" * 64,
        filename="paper.pdf",
        media_type="application/pdf",
        byte_size=2048,
    )


@pytest.fixture
def artifacts(tmp_path: Path) -> ArtifactStore:
    return ArtifactStore(f"file://{tmp_path}")


def settings(tmp_path: Path, concurrency: int) -> Settings:
    return Settings(
        source_store_url=f"file://{tmp_path}", index_batch_size=1, index_concurrency=concurrency
    )


def write_chunks(artifacts: ArtifactStore, count: int) -> None:
    artifacts.write_json(
        VERSION, GENERATION, CHUNKS_ARTIFACT, [chunk_payload(n) for n in range(count)]
    )


def test_batches_of_one_document_are_embedded_at_the_same_time(
    tmp_path: Path, artifacts: ArtifactStore, claim: JobClaim
) -> None:
    write_chunks(artifacts, 6)
    index = ConcurrentIndex(threading.Barrier(3, timeout=WAIT_SECONDS))

    EmbedStage(settings(tmp_path, 3), artifacts=artifacts, index=index)(claim)

    assert len(index.requests) == 6
    assert index.peak == 3


def test_no_more_batches_are_in_flight_than_configured(
    tmp_path: Path, artifacts: ArtifactStore, claim: JobClaim
) -> None:
    """The bound is what the operator sized their embedding server for."""
    write_chunks(artifacts, 12)
    index = ConcurrentIndex(threading.Barrier(2, timeout=WAIT_SECONDS))

    EmbedStage(settings(tmp_path, 2), artifacts=artifacts, index=index)(claim)

    assert len(index.requests) == 12
    assert index.peak == 2


class RefusingIndex(ConcurrentIndex):
    """Refuses the first batch; every later one takes long enough that the
    stage has seen the refusal before a free thread could reach another."""

    def index(self, request: IndexRequest) -> IndexResult:
        result = super().index(request)
        if len(self.requests) == 1:
            raise StageError("retrieval_unavailable", "Retrieval refused the batch.")
        time.sleep(0.2)
        return result


def test_a_refused_batch_fails_the_stage_without_sending_the_rest(
    tmp_path: Path, artifacts: ArtifactStore, claim: JobClaim
) -> None:
    """A Retrieval that has just refused a batch is not sent the next forty.

    The stage is retried as a whole, so anything sent after the first
    failure is work the retry repeats anyway.
    """
    write_chunks(artifacts, 40)
    index = RefusingIndex()

    with pytest.raises(StageError):
        EmbedStage(settings(tmp_path, 2), artifacts=artifacts, index=index)(claim)

    # The refused batch, plus at most one more per thread already started.
    assert len(index.requests) <= 3


class WaitingExtractor:
    """A model that will not answer until a later batch is being embedded."""

    def __init__(self, embedding: threading.Event) -> None:
        self.embedding = embedding

    def extract(self, chunks: Sequence[DocumentChunk]) -> list[ChunkGraph]:
        assert self.embedding.wait(WAIT_SECONDS), "no batch was embedded during extraction"
        return [ChunkGraph(chunk_id=chunk.chunk_id) for chunk in chunks]


class SignallingIndex(ConcurrentIndex):
    def __init__(self, embedding: threading.Event) -> None:
        super().__init__()
        self.embedding = embedding

    def index(self, request: IndexRequest) -> IndexResult:
        result = super().index(request)
        if len(self.requests) > 1:
            self.embedding.set()
        return result


def test_one_batch_is_embedded_while_another_is_extracted(
    tmp_path: Path, artifacts: ArtifactStore, claim: JobClaim
) -> None:
    """Graph extraction is the slowest model call in the pipeline.

    Run after each batch's embedding and before the next one's, it makes the
    embedding server wait on the extraction model and the other way round.
    """
    write_chunks(artifacts, 4)
    embedding = threading.Event()
    index = SignallingIndex(embedding)

    EmbedStage(
        settings(tmp_path, 2),
        artifacts=artifacts,
        index=index,
        extractor=WaitingExtractor(embedding),
    )(claim)

    assert len(index.graph_requests) == 4
