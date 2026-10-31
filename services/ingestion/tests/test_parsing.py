"""Parsing and chunking: what reaches the index, and what is refused.

Markdown exercises the whole path without a model download, so scoping,
ordering, limits, and containment are all covered offline. The two tests
that need Docling's PDF layout model are marked `models`; run
`pytest -m "not models"` without one.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from tempfile import gettempdir

import pytest
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from primer_ingestion.chunking import DocumentContext, chunk_id_for
from primer_ingestion.config import Settings
from primer_ingestion.errors import PermanentStageError, UnsupportedDocument
from primer_ingestion.parsing import DocumentParser, working_copy
from primer_storage import PPTX_MEDIA_TYPE

FIXTURES = Path(__file__).parent / "fixtures"

MARKDOWN = """# Retrieval Augmented Generation

Grounding answers in cited sources reduces unsupported claims.

## Evaluation

Recall at rank ten was the decisive metric for this corpus.
"""


@pytest.fixture
def context() -> DocumentContext:
    return DocumentContext(
        owner_user_id=uuid.uuid5(uuid.NAMESPACE_URL, "owner"),
        library_id=uuid.uuid5(uuid.NAMESPACE_URL, "library"),
        document_id=uuid.uuid5(uuid.NAMESPACE_URL, "document"),
        document_version_id=uuid.uuid5(uuid.NAMESPACE_URL, "version"),
        generation_id=uuid.uuid5(uuid.NAMESPACE_URL, "generation"),
        filename="paper.md",
    )


@pytest.fixture
def settings() -> Settings:
    """No tokenizer and no OCR: chunk by structure, and touch no network."""
    return Settings(chunk_tokenizer=None, enable_ocr=False)


@pytest.fixture
def parser(settings: Settings) -> DocumentParser:
    return DocumentParser(settings)


@pytest.fixture
def markdown(tmp_path: Path) -> Path:
    source = tmp_path / "paper.md"
    source.write_text(MARKDOWN)
    return source


def test_a_worker_that_never_parses_builds_no_parser(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every worker registers every stage, so every worker constructs this.

    That registration is deliberate - a process either knows how to run a
    stage or fails it loudly - but it means an index worker holds a
    `DocumentParser` it never calls. Building the chunker eagerly made that
    worker download a tokenizer, and only the parse deployment mounts a
    cache to put one in, so it crashlooped on its read-only root filesystem
    for a document it was never going to parse.
    """
    built: list[str] = []
    monkeypatch.setattr("primer_ingestion.parsing.build_chunker", lambda _: built.append("chunker"))
    monkeypatch.setattr(
        "primer_ingestion.parsing.build_converter", lambda **_: built.append("converter")
    )

    DocumentParser(Settings(broker_url="memory://"))

    assert built == []


def test_a_parser_that_is_used_builds_them_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deferred, not skipped - and still paid for only once.

    Docling loads models on first use, so rebuilding per document would put
    that cost on every job.
    """
    built: list[str] = []

    def record(name: str) -> object:
        built.append(name)
        return object()

    monkeypatch.setattr("primer_ingestion.parsing.build_chunker", lambda _: record("chunker"))
    monkeypatch.setattr("primer_ingestion.parsing.build_converter", lambda **_: record("converter"))
    parser = DocumentParser(Settings(broker_url="memory://"))

    first = (parser.chunker, parser.converter)
    for _ in range(3):
        assert (parser.chunker, parser.converter) == first

    assert built == ["chunker", "converter"]


def test_chunks_carry_the_scope_retrieval_filters_on(
    parser: DocumentParser, markdown: Path, context: DocumentContext
) -> None:
    """A chunk that cannot name its library cannot be isolated to one."""
    chunks = parser.parse_and_chunk(markdown, context, media_type="text/markdown")

    assert chunks
    assert all(chunk.library_id == context.library_id for chunk in chunks)
    assert all(chunk.document_version_id == context.document_version_id for chunk in chunks)
    assert all(chunk.generation_id == context.generation_id for chunk in chunks)
    assert all(chunk.owner_user_id == context.owner_user_id for chunk in chunks)
    assert all(chunk.filename == "paper.md" for chunk in chunks)


def test_ordinals_are_sequential_from_zero(
    parser: DocumentParser, markdown: Path, context: DocumentContext
) -> None:
    chunks = parser.parse_and_chunk(markdown, context, media_type="text/markdown")
    assert [chunk.ordinal for chunk in chunks] == list(range(len(chunks)))


def test_chunk_ids_are_stable_for_a_generation(context: DocumentContext) -> None:
    """Re-running a stage rewrites the same ids rather than doubling the index."""
    assert chunk_id_for(context, 3) == chunk_id_for(context, 3)
    assert chunk_id_for(context, 3) != chunk_id_for(context, 4)


def test_a_new_generation_gives_new_chunk_ids(context: DocumentContext) -> None:
    """A rebuild must not collide with the generation still being searched."""
    rebuilt = DocumentContext(
        owner_user_id=context.owner_user_id,
        library_id=context.library_id,
        document_id=context.document_id,
        document_version_id=context.document_version_id,
        generation_id=uuid.uuid5(uuid.NAMESPACE_URL, "second-generation"),
        filename=context.filename,
    )
    assert chunk_id_for(rebuilt, 0) != chunk_id_for(context, 0)


def test_quoted_content_is_verbatim_and_embedding_text_is_not(
    parser: DocumentParser, markdown: Path, context: DocumentContext
) -> None:
    """A citation must be findable in the source; an embedding need not be."""
    chunks = parser.parse_and_chunk(markdown, context, media_type="text/markdown")
    body = next(c for c in chunks if "unsupported claims" in c.content)

    assert body.content in MARKDOWN
    assert body.content.startswith("Grounding")
    assert "Retrieval Augmented Generation" in body.embedding_text


def test_headings_become_the_section_locator(
    parser: DocumentParser, markdown: Path, context: DocumentContext
) -> None:
    chunks = parser.parse_and_chunk(markdown, context, media_type="text/markdown")
    sections = {chunk.locator.section for chunk in chunks}

    assert "Retrieval Augmented Generation" in sections
    assert any(section and "Evaluation" in section for section in sections)


def test_a_document_with_no_text_is_refused(
    parser: DocumentParser, tmp_path: Path, context: DocumentContext
) -> None:
    """Indexing nothing would report success for a document nobody can search."""
    empty = tmp_path / "empty.md"
    empty.write_text("   \n\n \n")

    with pytest.raises(UnsupportedDocument) as raised:
        parser.parse_and_chunk(empty, context, media_type="text/markdown")
    assert raised.value.code == "ocr_required"


def test_an_oversized_document_is_refused_rather_than_truncated(
    markdown: Path, context: DocumentContext
) -> None:
    """Half a document would answer questions with no sign the rest was dropped."""
    parser = DocumentParser(Settings(chunk_tokenizer=None, max_chunks_per_document=1))

    with pytest.raises(PermanentStageError) as raised:
        parser.parse_and_chunk(markdown, context, media_type="text/markdown")
    assert raised.value.code == "too_many_chunks"


def test_a_format_primer_does_not_claim_is_refused(
    parser: DocumentParser, markdown: Path, context: DocumentContext
) -> None:
    """Docling reads far more than Primer supports; the rest is out of scope."""
    with pytest.raises(UnsupportedDocument) as raised:
        parser.parse_and_chunk(markdown, context, media_type="text/html")
    assert raised.value.code == "unsupported_media_type"


def test_a_document_over_its_time_budget_is_refused(
    markdown: Path, context: DocumentContext
) -> None:
    parser = DocumentParser(Settings(chunk_tokenizer=None, parse_deadline_seconds=1e-9))

    with pytest.raises(PermanentStageError) as raised:
        parser.parse_and_chunk(markdown, context, media_type="text/markdown")
    assert raised.value.code == "parse_timeout"


def test_conversion_gets_a_private_read_only_copy(tmp_path: Path) -> None:
    """Untrusted input is converted away from the shared source object."""
    source = tmp_path / "source.md"
    source.write_text("original")

    with working_copy(source, ".md") as copy:
        assert copy != source
        assert copy.read_text() == "original"
        with pytest.raises(PermissionError):
            copy.write_text("modified")
        directory = copy.parent

    assert not directory.exists()
    assert source.read_text() == "original"


def test_no_working_directories_survive_a_failure(
    parser: DocumentParser, tmp_path: Path, context: DocumentContext
) -> None:
    """A rejected document must not leave a copy of itself on disk."""
    empty = tmp_path / "empty.md"
    empty.write_text("  ")
    before = set(Path(gettempdir()).glob("primer-parse-*"))

    with pytest.raises(UnsupportedDocument):
        parser.parse_and_chunk(empty, context, media_type="text/markdown")

    assert set(Path(gettempdir()).glob("primer-parse-*")) == before


@pytest.mark.models
def test_pdf_chunks_keep_page_and_scope(parser: DocumentParser, context: DocumentContext) -> None:
    """The plan's case: a real PDF, with citations that point somewhere."""
    chunks = parser.parse_and_chunk(
        FIXTURES / "text-paper.pdf", context, media_type="application/pdf"
    )

    assert chunks
    assert all(chunk.library_id == context.library_id for chunk in chunks)
    assert any(chunk.locator.page == 1 for chunk in chunks)
    assert any("Recall at rank ten" in chunk.content for chunk in chunks)


def test_a_document_of_only_headings_still_indexes(
    parser: DocumentParser, tmp_path: Path, context: DocumentContext
) -> None:
    """A title slide is not an empty document.

    Chunkers treat a heading as context for the text beneath it, so a
    document that is only headings chunks to nothing. Its text is plainly
    there, and reporting it as unreadable would be wrong.
    """
    headings = tmp_path / "outline.md"
    headings.write_text("# Quarterly Review\n\n## Revenue\n\n## Risks\n")

    chunks = parser.parse_and_chunk(headings, context, media_type="text/markdown")

    assert [chunk.content for chunk in chunks] == ["Quarterly Review", "Revenue", "Risks"]


@pytest.mark.models
def test_slides_are_parsed_with_a_page_per_slide(
    parser: DocumentParser, context: DocumentContext
) -> None:
    """Decks carry real content, and a citation needs to name the slide."""
    chunks = parser.parse_and_chunk(FIXTURES / "slides.pptx", context, media_type=PPTX_MEDIA_TYPE)

    assert [chunk.locator.page for chunk in chunks] == [1, 2]
    assert any("unsupported claims" in chunk.content for chunk in chunks)
    assert chunks[1].locator.section == "Evaluation"


@pytest.mark.models
def test_ocr_reads_a_page_that_is_only_an_image(context: DocumentContext) -> None:
    """The point of enabling OCR: text that exists only as pixels."""
    parser = DocumentParser(Settings(chunk_tokenizer=None, enable_ocr=True))

    chunks = parser.parse_and_chunk(
        FIXTURES / "scanned-with-text.pdf", context, media_type="application/pdf"
    )

    assert chunks
    assert "SCANNED" in chunks[0].content


@pytest.mark.models
def test_the_same_scan_is_unreadable_without_ocr(
    parser: DocumentParser, context: DocumentContext
) -> None:
    """The two codes exist because they call for different answers."""
    with pytest.raises(UnsupportedDocument) as raised:
        parser.parse_and_chunk(
            FIXTURES / "scanned-with-text.pdf", context, media_type="application/pdf"
        )
    assert raised.value.code == "ocr_required"


@pytest.mark.models
def test_a_page_with_no_text_at_all_is_refused_even_with_ocr(
    context: DocumentContext,
) -> None:
    """OCR ran and found nothing, which is a different answer to the user."""
    parser = DocumentParser(Settings(chunk_tokenizer=None, enable_ocr=True))

    with pytest.raises(UnsupportedDocument) as raised:
        parser.parse_and_chunk(
            FIXTURES / "scanned-paper.pdf", context, media_type="application/pdf"
        )
    assert raised.value.code == "no_text_found"


def test_docling_models_run_where_the_worker_was_told(monkeypatch: pytest.MonkeyPatch) -> None:
    """Left to itself torch takes every core on the node, not the pod's share.

    On a large node that is dozens of threads contending for the two cores
    the limit grants, which is slower than using two.
    """
    monkeypatch.setattr("primer_ingestion.parsing.build_chunker", lambda _: object())
    parser = DocumentParser(Settings(broker_url="memory://", parse_device="cpu", parse_threads=3))

    options = parser.converter.format_to_options[InputFormat.PDF].pipeline_options
    assert isinstance(options, PdfPipelineOptions)
    assert options.accelerator_options.device == "cpu"
    assert options.accelerator_options.num_threads == 3
