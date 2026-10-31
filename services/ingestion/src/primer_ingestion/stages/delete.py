"""The delete stage: removing what a tombstoned document left behind.

Order is the whole design. Passages go first, because they are what a search
can still reach. Metadata goes next, and only then the stored bytes - a
source object is shared between every document holding identical content, so
it may only be removed once the database says nothing references it.

Every step is safe to repeat. This message can be redelivered, and a
half-finished cleanup must be finishable rather than a state someone has to
repair by hand.
"""

from __future__ import annotations

import logging
from typing import Protocol

from primer_contracts.indexing import PurgeRequest
from primer_contracts.ingestion import JobClaim
from primer_storage import ArtifactStore, SourceStore

from primer_ingestion.config import Settings
from primer_ingestion.control_client import ControlClient, JobCleanup
from primer_ingestion.retrieval_client import (
    RetrievalClient,
    VectorIndex,
    worker_principal,
)

logger = logging.getLogger(__name__)


class SourceRemover(Protocol):
    """The one storage operation cleanup performs."""

    def remove(self, sha256: str) -> None: ...


class DeleteStage:
    def __init__(
        self,
        settings: Settings,
        control: JobCleanup | None = None,
        index: VectorIndex | None = None,
        sources: SourceRemover | None = None,
        artifacts: ArtifactStore | None = None,
    ) -> None:
        self._settings = settings
        self._control = control or ControlClient(settings)
        self._index = index or RetrievalClient(settings)
        self._sources = sources or SourceStore(
            settings.source_store_url, max_bytes=settings.max_source_bytes
        )
        self._artifacts = artifacts or ArtifactStore(settings.source_store_url)

    def __call__(self, claim: JobClaim) -> None:
        # Every generation, not just the current one: the builds a version
        # has been through are not recorded, and cleanup must leave nothing.
        self._index.purge(
            PurgeRequest(
                principal=worker_principal(claim.owner_user_id),
                library_id=claim.library_id,
                document_version_id=claim.document_version_id,
                keep_generation_id=None,
            )
        )

        freed = self._control.purge(claim.job_id)

        # The owner's conversion goes even when the bytes stay: it is a copy
        # of their text, and another holder of the same bytes cannot read it.
        # If the owner still has another copy, its next parse converts again.
        self._artifacts.discard_conversions(claim.source_sha256, claim.owner_user_id)

        # Last, and only for hashes the database says nothing references.
        for sha256 in freed:
            self._artifacts.discard_conversions(sha256)
            self._sources.remove(sha256)
        self._artifacts.discard_generation(claim.document_version_id, claim.generation_id)

        logger.info(
            "job %s: purged document %s and %d source objects",
            claim.job_id,
            claim.document_id,
            len(freed),
        )
