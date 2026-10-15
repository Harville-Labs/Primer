"""Document conversion, and the limits it runs under.

Conversion is Docling's `DocumentConverter`. Primer adds no parser of its
own: extracting structure from PDF and DOCX is exactly the problem Docling
exists to solve, and a regex fallback here would produce citations that
point at nothing.

What Primer does add is containment. A converted document is untrusted
input, so conversion runs against a private copy in a temporary directory
that is removed on every path out, and its cost is bounded before its
results are used.
"""

from __future__ import annotations

import logging
import shutil
import stat
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from docling.datamodel.base_models import InputFormat
from docling.datamodel.document import ConversionStatus
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling_core.transforms.chunker import BaseChunker
from primer_contracts.chunks import DocumentChunk
from primer_storage import DOCX_MEDIA_TYPE, PDF_MEDIA_TYPE, PPTX_MEDIA_TYPE

from primer_ingestion.chunking import DocumentContext, build_chunker, to_chunks
from primer_ingestion.config import Settings
from primer_ingestion.errors import PermanentStageError, StageError, UnsupportedDocument
from primer_ingestion.pictures import PictureReader, build_picture_reader, read_pictures

logger = logging.getLogger(__name__)

#: Only the formats Primer claims to support. Docling reads many more, and
#: allowing them here would accept uploads the rest of the system - quotas,
#: citations, the UI - has never been designed for.
FORMATS_BY_MEDIA_TYPE: dict[str, InputFormat] = {
    PDF_MEDIA_TYPE: InputFormat.PDF,
    DOCX_MEDIA_TYPE: InputFormat.DOCX,
    PPTX_MEDIA_TYPE: InputFormat.PPTX,
    "text/markdown": InputFormat.MD,
    "text/plain": InputFormat.MD,
}

#: Extensions Docling infers format from. The source object is stored under
#: its hash, so the working copy has to be renamed to something Docling can
#: recognize before conversion.
EXTENSIONS: dict[InputFormat, str] = {
    InputFormat.PDF: ".pdf",
    InputFormat.DOCX: ".docx",
    InputFormat.PPTX: ".pptx",
    InputFormat.MD: ".md",
}


def build_converter(*, enable_ocr: bool = True) -> DocumentConverter:
    """A converter matching what Primer claims to support.

    OCR is on, and reaches exactly one format. `do_ocr` is an option on the
    PDF pipeline; every other format here is converted by `SimplePipeline`,
    which has no OCR setting at all - so this argument says nothing about a
    PowerPoint. Whatever a slide keeps inside a picture is read afterwards,
    by `read_pictures`, or not at all.

    This docstring used to claim the opposite, and a deck of pasted charts
    was accepted, reported ready, and indexed as its titles.

    The cost is real and worth stating: recognized text is a transcription,
    not the document's own characters, so a citation drawn from it can be
    subtly wrong in a way a reader cannot see. Chunks carry no marker for
    this yet, which is the honest limitation of turning it on.
    """
    pdf_options = PdfPipelineOptions(do_ocr=enable_ocr, do_table_structure=True)
    return DocumentConverter(
        allowed_formats=sorted(set(FORMATS_BY_MEDIA_TYPE.values()), key=lambda f: f.value),
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pdf_options)},
    )


@contextmanager
def working_copy(source: Path, suffix: str) -> Generator[Path]:
    """A private, read-only copy of the source, deleted on every path out.

    Conversion is the first code to touch bytes a stranger uploaded. It gets
    its own directory so a converter that writes beside its input cannot
    reach anything else, and a read-only copy so the original object - shared
    with every other document holding the same bytes - cannot be modified.
    """
    with TemporaryDirectory(prefix="primer-parse-") as directory:
        target = Path(directory) / f"source{suffix}"
        shutil.copyfile(source, target)
        target.chmod(stat.S_IRUSR)
        try:
            yield target
        finally:
            # TemporaryDirectory cannot remove a read-only file on every
            # platform, so the mode is restored before it tries.
            target.chmod(stat.S_IRUSR | stat.S_IWUSR)


class DocumentParser:
    """Converts and chunks one document at a time.

    The converter and chunker are built once and reused: Docling loads
    models on first use, and rebuilding per job would pay that cost on every
    document.

    Built on first use rather than in `__init__`, because every ingestion
    worker registers every stage - that is deliberate, so a process either
    knows how to run a stage or fails it loudly rather than silently having
    no handler - and constructing this eagerly made a worker that only
    embeds and indexes pay for a parser it never calls. It is not only a
    slower start: the chunker downloads a tokenizer, and only the parse
    deployment mounts a cache to hold one, so an index worker given the same
    configuration crashlooped against its read-only root filesystem.
    """

    def __init__(
        self,
        settings: Settings,
        converter: DocumentConverter | None = None,
        chunker: BaseChunker | None = None,
    ) -> None:
        self._settings = settings
        self._given_converter = converter
        self._given_chunker = chunker
        self._given_picture_reader: PictureReader | None = None

    @property
    def converter(self) -> DocumentConverter:
        if self._given_converter is None:
            self._given_converter = build_converter(enable_ocr=self._settings.enable_ocr)
        return self._given_converter

    @property
    def chunker(self) -> BaseChunker:
        if self._given_chunker is None:
            self._given_chunker = build_chunker(self._settings)
        return self._given_chunker

    @property
    def picture_reader(self) -> PictureReader:
        if self._given_picture_reader is None:
            self._given_picture_reader = build_picture_reader()
        return self._given_picture_reader

    def _reads_pictures(self, input_format: InputFormat) -> bool:
        """Whether this format needs its pictures opened separately.

        Only the PDF pipeline does OCR of its own, so PDFs are excluded -
        running over them again would recognize the same text twice and
        charge for it. Everything else Primer accepts is converted by
        `SimplePipeline`, which has no OCR at any setting: a slide deck of
        pasted charts, or a report whose figures carry its numbers,
        otherwise converts to headings and empty images.
        """
        if not self._settings.enable_ocr:
            return False
        return input_format is not InputFormat.PDF

    def format_for(self, media_type: str) -> InputFormat:
        input_format = FORMATS_BY_MEDIA_TYPE.get(media_type)
        if input_format is None:
            raise UnsupportedDocument(
                "unsupported_media_type",
                f"Primer cannot parse {media_type}.",
            )
        return input_format

    def parse_and_chunk(
        self, source: Path, context: DocumentContext, *, media_type: str
    ) -> list[DocumentChunk]:
        """Convert a stored source into scoped chunks.

        The deadline is checked between phases rather than enforced by
        interrupting the converter. Docling's work happens inside native
        extensions, and killing a thread mid-call there risks a corrupt
        process; the hard stop is the worker's task time limit, which kills
        the process and lets the job's lease expire.
        """
        input_format = self.format_for(media_type)
        started = time.monotonic()

        with working_copy(source, EXTENSIONS[input_format]) as copy:
            try:
                result = self.converter.convert(copy, raises_on_error=False)
            except Exception as error:
                logger.exception("conversion failed for %s", context.document_version_id)
                raise StageError("conversion_failed", "The document could not be converted.") from (
                    error
                )

            if result.status is ConversionStatus.FAILURE:
                raise UnsupportedDocument(
                    "conversion_failed",
                    "The document could not be read. It may be corrupt or password protected.",
                )
            self._check_deadline(started, "conversion")

            if self._reads_pictures(input_format):
                read = read_pictures(
                    result.document,
                    self.picture_reader,
                    max_pictures=self._settings.max_pictures_per_document,
                )
                if read:
                    logger.info(
                        "read text from %d picture(s) in %s", read, context.document_version_id
                    )
                self._check_deadline(started, "read pictures")

            chunks = to_chunks(
                result.document,
                self.chunker,
                context,
                max_chunks=self._settings.max_chunks_per_document,
                ocr_attempted=self._settings.enable_ocr,
                min_chars=self._settings.min_chunk_chars,
            )
            self._check_deadline(started, "chunking")
            return chunks

    def _check_deadline(self, started: float, phase: str) -> None:
        elapsed = time.monotonic() - started
        if elapsed > self._settings.parse_deadline_seconds:
            raise PermanentStageError(
                "parse_timeout",
                f"The document took longer than {self._settings.parse_deadline_seconds:.0f}s "
                f"to {phase}.",
            )
