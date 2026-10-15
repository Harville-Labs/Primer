"""The index stage: confirm the generation is complete before it goes live.

This stage writes nothing. Its whole job is to refuse. Completing it is what
activates the generation in Control, so anything wrong that gets past here
becomes the answer users receive.
"""

from __future__ import annotations

import logging

from primer_contracts.indexing import GenerationQuery, PurgeRequest
from primer_contracts.ingestion import JobClaim
from primer_storage import ArtifactStore

from primer_ingestion.config import Settings
from primer_ingestion.errors import PermanentStageError, StageError
from primer_ingestion.retrieval_client import RetrievalClient, VectorIndex, worker_principal
from primer_ingestion.stages.parse import CHUNKS_ARTIFACT

logger = logging.getLogger(__name__)


class IndexStage:
    def __init__(
        self,
        settings: Settings,
        artifacts: ArtifactStore | None = None,
        index: VectorIndex | None = None,
    ) -> None:
        self._settings = settings
        self._artifacts = artifacts or ArtifactStore(settings.source_store_url)
        self._index = index or RetrievalClient(settings)

    def __call__(self, claim: JobClaim) -> None:
        try:
            expected = len(
                self._artifacts.read_json(
                    claim.document_version_id, claim.generation_id, CHUNKS_ARTIFACT
                )
            )
        except FileNotFoundError as error:
            raise PermanentStageError(
                "chunks_missing",
                "The parsed chunks for this generation are no longer available.",
            ) from error

        verified = self._index.verify(
            GenerationQuery(
                principal=worker_principal(claim.owner_user_id),
                library_id=claim.library_id,
                document_version_id=claim.document_version_id,
                generation_id=claim.generation_id,
            )
        )
        actual = verified.count

        if actual != expected:
            # Retryable: the usual cause is a batch that failed and can be
            # written again. Activating anyway would drop the missing
            # passages from every future answer with nothing to show for it.
            raise StageError(
                "incomplete_index",
                f"The index holds {actual} of {expected} passages.",
            )
        if self._settings.graph_extraction_enabled:
            self._require_graph(verified.graph_count, expected)
        logger.info("job %s: generation complete with %d chunks", claim.job_id, expected)

    def _require_graph(self, actual: int | None, expected: int) -> None:
        """The graph is held to the same standard as the vectors.

        A generation activated with part of its graph would answer graph
        questions from part of the document, and nothing would say so.
        """
        if actual is None:
            # Embed wrote a graph and Retrieval now says it keeps none: the
            # two services disagree about configuration, and no retry
            # changes that.
            raise PermanentStageError(
                "graph_not_configured",
                "Graph extraction is on, but Retrieval has no graph store. Set "
                "PRIMER_GRAPH_URL on Retrieval, or unset PRIMER_GRAPH_EXTRACTION_MODEL.",
            )
        if actual != expected:
            raise StageError(
                "incomplete_graph",
                f"The knowledge graph holds {actual} of {expected} passages.",
            )

    def retire_superseded(self, claim: JobClaim) -> None:
        """Drop earlier builds of this version, once this one is live.

        Called after activation rather than before it: until Control has
        switched, the older generation is what searches are reading, and
        removing it early would empty the index a user is querying right now.
        """
        self._index.purge(
            PurgeRequest(
                principal=worker_principal(claim.owner_user_id),
                library_id=claim.library_id,
                document_version_id=claim.document_version_id,
                keep_generation_id=claim.generation_id,
            )
        )
