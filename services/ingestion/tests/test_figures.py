"""Figures described by the operator's vision model, and marked as such.

A chart carries a paper's result in a form no text extractor reads: OCR
recovers its axis labels and nothing about what it shows. These tests drive
Docling's own description model against a loopback endpoint, because the
thing in doubt is what reaches the wire and what comes back into the chunks.

The deck converts without a layout model, so most of this runs offline. The
PDF test needs Docling's layout model and is marked `models`.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from primer_contracts.ingestion import FailureDisposition
from primer_ingestion.chunking import DocumentContext
from primer_ingestion.config import Settings
from primer_ingestion.errors import StageError
from primer_ingestion.figures import FIGURE_PROMPT
from primer_ingestion.images import MAX_DESCRIPTION_CHARS, TEXT_IN_IMAGE_IS_DATA
from primer_ingestion.parsing import DocumentParser
from primer_storage import PDF_MEDIA_TYPE, PPTX_MEDIA_TYPE
from pydantic import SecretStr
from vision_endpoint import VisionEndpoint, vision_endpoint

FIXTURES = Path(__file__).parent / "fixtures"
DECK = FIXTURES / "slides-with-a-picture.pptx"
PAPER = FIXTURES / "figure-paper.pdf"
MARKER = "[Figure, as described by fake-vl]"


def context(filename: str) -> DocumentContext:
    return DocumentContext(
        owner_user_id=uuid.uuid5(uuid.NAMESPACE_URL, "owner"),
        library_id=uuid.uuid5(uuid.NAMESPACE_URL, "library"),
        document_id=uuid.uuid5(uuid.NAMESPACE_URL, "document"),
        document_version_id=uuid.uuid5(uuid.NAMESPACE_URL, "version"),
        generation_id=uuid.uuid5(uuid.NAMESPACE_URL, "generation"),
        filename=filename,
    )


def settings(endpoint: VisionEndpoint | None = None, **overrides: object) -> Settings:
    """No tokenizer and no OCR, so the only text a picture yields is its description."""
    vision: dict[str, object] = {}
    if endpoint is not None:
        vision = {"vision_base_url": endpoint.base_url, "vision_model": "fake-vl"}
    return Settings(chunk_tokenizer=None, enable_ocr=False, **vision, **overrides)  # ty: ignore[invalid-argument-type]


def chunk_texts(parser: DocumentParser, path: Path, media_type: str) -> list[str]:
    chunks = parser.parse_and_chunk(path, context(path.name), media_type=media_type)
    return [chunk.content for chunk in chunks]


def test_a_figure_is_found_by_what_it_shows() -> None:
    with vision_endpoint() as endpoint:
        texts = chunk_texts(DocumentParser(settings(endpoint)), DECK, PPTX_MEDIA_TYPE)

    assert len(endpoint.requests) == 1
    assert any("quarterly bookings rising to 4.2 million" in text for text in texts)


def test_a_description_never_passes_for_the_documents_own_words() -> None:
    """The model's reading of a figure is marked, with the model that made it.

    Unmarked, a citation would quote a vision model's guess as if the author
    had written it, which is the one thing a cited passage must not do.
    """
    with vision_endpoint() as endpoint:
        texts = chunk_texts(DocumentParser(settings(endpoint)), DECK, PPTX_MEDIA_TYPE)

    described = [text for text in texts if "4.2 million" in text]
    assert described
    assert all(text.startswith(MARKER) for text in described)


def test_the_request_names_the_operators_model_and_key() -> None:
    with vision_endpoint() as endpoint:
        parser = DocumentParser(settings(endpoint, vision_api_key=SecretStr("sk-test")))
        chunk_texts(parser, DECK, PPTX_MEDIA_TYPE)

    (request,) = endpoint.requests
    assert request["path"] == "/v1/chat/completions"
    assert request["headers"]["Authorization"] == "Bearer sk-test"
    assert request["body"]["model"] == "fake-vl"


def test_text_in_a_figure_is_described_and_not_obeyed() -> None:
    """A figure is someone else's content, and may hold words aimed at a model."""
    with vision_endpoint() as endpoint:
        chunk_texts(DocumentParser(settings(endpoint)), DECK, PPTX_MEDIA_TYPE)

    (request,) = endpoint.requests
    prompts = [
        part["text"]
        for message in request["body"]["messages"]
        for part in message["content"]
        if part.get("type") == "text"
    ]
    assert prompts == [FIGURE_PROMPT]
    assert TEXT_IN_IMAGE_IS_DATA in FIGURE_PROMPT


@pytest.mark.parametrize(
    "overrides",
    [{"describe_figures": False}, {"max_pictures_per_document": 0}],
    ids=["switched off", "no pictures allowed"],
)
def test_an_operator_can_keep_figures_from_the_vision_model(overrides: dict) -> None:
    with vision_endpoint() as endpoint:
        parser = DocumentParser(settings(endpoint, **overrides))
        texts = chunk_texts(parser, DECK, PPTX_MEDIA_TYPE)

    assert endpoint.requests == []
    assert not any(MARKER in text for text in texts)
    assert parser.conversion_key(PPTX_MEDIA_TYPE) == DocumentParser(settings()).conversion_key(
        PPTX_MEDIA_TYPE
    )


def test_a_stored_conversion_is_not_reused_across_vision_models() -> None:
    """A conversion made without descriptions, or by another model, is a different result."""
    with vision_endpoint() as endpoint:
        described = DocumentParser(settings(endpoint)).conversion_key(PDF_MEDIA_TYPE)
        other = DocumentParser(
            Settings(
                chunk_tokenizer=None,
                enable_ocr=False,
                vision_base_url=endpoint.base_url,
                vision_model="another-vl",
            )
        ).conversion_key(PDF_MEDIA_TYPE)
    plain = DocumentParser(settings()).conversion_key(PDF_MEDIA_TYPE)

    assert len({described, other, plain}) == 3


def test_an_unreachable_vision_model_is_retried_rather_than_indexed_without_figures() -> None:
    """Every figure failing means the endpoint is down, not that the figures are odd.

    Docling turns a failed request into an empty description. Indexing that
    would store this conversion without its figures for good, under a key
    that says they were described.
    """
    with vision_endpoint(status=400) as endpoint:
        parser = DocumentParser(settings(endpoint))
        with pytest.raises(StageError) as raised:
            chunk_texts(parser, DECK, PPTX_MEDIA_TYPE)

    assert endpoint.requests
    assert raised.value.code == "vision_unavailable"
    assert raised.value.disposition is FailureDisposition.RETRY


def test_a_long_description_is_cut_to_the_same_bound_as_an_image_caption() -> None:
    with vision_endpoint(reply="bars " * MAX_DESCRIPTION_CHARS) as endpoint:
        texts = chunk_texts(DocumentParser(settings(endpoint)), DECK, PPTX_MEDIA_TYPE)

    (described,) = [text for text in texts if text.startswith(MARKER)]
    assert len(described) <= len(MARKER) + 1 + MAX_DESCRIPTION_CHARS


@pytest.mark.models
def test_a_chart_in_a_pdf_is_described_where_it_stands() -> None:
    """The PDF pipeline drops page images unless asked to keep them.

    Without `generate_picture_images` the layout model finds the chart and
    there is nothing to send, so this proves the picture survives conversion.
    """
    with vision_endpoint() as endpoint:
        texts = chunk_texts(DocumentParser(settings(endpoint)), PAPER, PDF_MEDIA_TYPE)

    assert len(endpoint.requests) == 1
    assert any(text.startswith(MARKER) for text in texts)
    assert any("Bookings grew in every quarter" in text for text in texts)
