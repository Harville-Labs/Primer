"""The embed stage: chunks from the parse artifact into a pending generation.

Embedding happens inside Retrieval, not here. Retrieval owns the store and
therefore owns the vector width, the model, and the endpoint; a worker that
embedded independently could write vectors the store cannot compare.

Graph extraction does happen here, when it is on. It is a model call per
passage - slow, and failing in ways worth retrying - which is work for a
worker with a retry budget rather than for a synchronous request to
Retrieval that a timeout would throw away.

Batches are sent several at a time, and each one's graph is extracted as
soon as its passages are indexed, so the extraction model and the embedding
model work at the same time rather than taking turns. Order does not
matter: chunk ids are deterministic, so a batch written twice or out of
turn overwrites rather than duplicates.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from itertools import batched

from primer_contracts.chunks import DocumentChunk
from primer_contracts.identity import Principal
from primer_contracts.indexing import GraphIndexRequest, IndexRequest
from primer_contracts.ingestion import JobClaim
from primer_storage import ArtifactStore

from primer_ingestion.config import Settings
from primer_ingestion.errors import PermanentStageError
from primer_ingestion.extraction import GraphExtractor, build_graph_extractor
from primer_ingestion.retrieval_client import (
    RetrievalClient,
    VectorIndex,
    worker_principal,
)
from primer_ingestion.stages.parse import CHUNKS_ARTIFACT

logger = logging.getLogger(__name__)


class EmbedStage:
    def __init__(
        self,
        settings: Settings,
        artifacts: ArtifactStore | None = None,
        index: VectorIndex | None = None,
        extractor: GraphExtractor | None = None,
    ) -> None:
        self._settings = settings
        self._artifacts = artifacts or ArtifactStore(settings.source_store_url)
        self._index = index or RetrievalClient(settings)
        self._extractor = extractor or build_graph_extractor(settings)

    def __call__(self, claim: JobClaim) -> None:
        chunks = self._load(claim)

        principal = worker_principal(claim.owner_user_id)
        batches = batched(chunks, self._settings.index_batch_size, strict=False)
        with ThreadPoolExecutor(max_workers=self._settings.index_concurrency) as pool:
            # Consumed, not just submitted, so the first refused batch raises
            # here - and `map` cancels every batch it has not started when
            # that happens, so a Retrieval that has just refused part of a
            # document is not sent the rest. A retry rewrites every batch;
            # the ones that landed are overwritten in place.
            for _ in pool.map(lambda batch: self._index_batch(claim, principal, batch), batches):
                pass
        logger.info("job %s: indexed %d chunks", claim.job_id, len(chunks))

    def _index_batch(
        self, claim: JobClaim, principal: Principal, batch: Sequence[DocumentChunk]
    ) -> None:
        self._index.index(
            IndexRequest(
                principal=principal,
                library_id=claim.library_id,
                document_version_id=claim.document_version_id,
                generation_id=claim.generation_id,
                chunks=tuple(batch),
            )
        )
        if self._extractor is not None:
            self._index.index_graph(
                GraphIndexRequest(
                    principal=principal,
                    library_id=claim.library_id,
                    document_version_id=claim.document_version_id,
                    generation_id=claim.generation_id,
                    graphs=tuple(self._extractor.extract(batch)),
                )
            )

    def _load(self, claim: JobClaim) -> list[DocumentChunk]:
        try:
            payload = self._artifacts.read_json(
                claim.document_version_id, claim.generation_id, CHUNKS_ARTIFACT
            )
        except FileNotFoundError as error:
            # The parse stage completed, so this artifact should exist. If it
            # does not, this generation cannot be rebuilt from here and
            # retrying the embed stage forever would not help.
            raise PermanentStageError(
                "chunks_missing",
                "The parsed chunks for this generation are no longer available.",
            ) from error
        return [DocumentChunk.model_validate(item) for item in payload]
