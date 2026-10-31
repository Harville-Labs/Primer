"""The parse stage: source bytes in, scoped chunks out.

Chunks are written to the artifact store rather than returned, because the
stage that consumes them runs in a different process and receives only a job
id. Keying the artifact by generation means a rebuild writes a new file
instead of overwriting the one a running search still depends on.

Docling's output is kept as well, keyed by the source's hash. Conversion is
nearly all of a parse's cost, and the same bytes arrive again more often
than it seems: a paper uploaded to two libraries, a file re-uploaded after a
delete-and-retry, every document after a change to chunking settings. The
chunks themselves cannot be shared that way, because they carry the
version, library, and owner they belong to.

A conversion is reused only by the owner who caused it. Sharing it across
owners would be cheaper and would undo a promise the source store keeps:
that nobody can learn whether someone else uploaded the same file. A
document that became ready in a second instead of a minute would say so.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from docling_core.types.doc import DoclingDocument
from primer_contracts.ingestion import JobClaim
from primer_storage import ArtifactStore, SourceStore
from pydantic import ValidationError

from primer_ingestion.chunking import DocumentContext
from primer_ingestion.config import Settings
from primer_ingestion.errors import StageError
from primer_ingestion.parsing import DocumentParser

logger = logging.getLogger(__name__)

#: The artifact the embed stage reads.
CHUNKS_ARTIFACT = "chunks.json"


class ParseStage:
    """Holds the parser across jobs so Docling's models load once."""

    def __init__(
        self,
        settings: Settings,
        parser: DocumentParser | None = None,
        sources: SourceStore | None = None,
        artifacts: ArtifactStore | None = None,
    ) -> None:
        self._settings = settings
        self._parser = parser or DocumentParser(settings)
        self._sources = sources or SourceStore(
            settings.source_store_url, max_bytes=settings.max_source_bytes
        )
        self._artifacts = artifacts or ArtifactStore(settings.source_store_url)

    def __call__(self, claim: JobClaim) -> None:
        context = DocumentContext(
            owner_user_id=claim.owner_user_id,
            library_id=claim.library_id,
            document_id=claim.document_id,
            document_version_id=claim.document_version_id,
            generation_id=claim.generation_id,
            filename=claim.filename,
        )

        started = time.monotonic()
        made_with = self._parser.conversion_key(claim.media_type)
        document = self._stored_conversion(claim, made_with)
        if document is None:
            document = self._convert(claim, context, started)
            self._artifacts.write_conversion(
                claim.source_sha256,
                claim.owner_user_id,
                made_with,
                document.model_dump_json().encode(),
            )
        else:
            logger.info("job %s: reusing the conversion of %s", claim.job_id, claim.source_sha256)
        chunks = self._parser.chunk(document, context, started=started)

        self._artifacts.write_json(
            claim.document_version_id,
            claim.generation_id,
            CHUNKS_ARTIFACT,
            [chunk.model_dump(mode="json") for chunk in chunks],
        )
        logger.info(
            "job %s: parsed %s into %d chunks", claim.job_id, claim.document_id, len(chunks)
        )

    def _stored_conversion(self, claim: JobClaim, made_with: str) -> DoclingDocument | None:
        sha256 = claim.source_sha256
        payload = self._artifacts.read_conversion(sha256, claim.owner_user_id, made_with)
        if payload is None:
            return None
        try:
            return DoclingDocument.model_validate_json(payload)
        except ValidationError:
            # A write cut short, or a Docling upgrade that kept its version
            # string. Converting again is always correct; the overwrite below
            # repairs the entry.
            logger.warning("the stored conversion of %s is unreadable; converting again", sha256)
            return None

    def _convert(
        self, claim: JobClaim, context: DocumentContext, started: float
    ) -> DoclingDocument:
        with TemporaryDirectory(prefix="primer-source-") as directory:
            local = Path(directory) / "source"
            try:
                self._sources.download(claim.source_sha256, local)
            except FileNotFoundError as error:
                # The metadata says these bytes are durable, so their absence
                # is an operational fault, not a bad document. Retrying is
                # right: object stores are eventually consistent and mounts
                # come back.
                raise StageError(
                    "source_unavailable", "The stored source could not be read."
                ) from error
            return self._parser.convert(
                local, context, media_type=claim.media_type, started=started
            )
