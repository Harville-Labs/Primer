"""Format detection, which decides what reaches the parser."""

from __future__ import annotations

import pytest
from primer_storage import (
    DOCX_MEDIA_TYPE,
    FORMAT_NAMES,
    IMAGE_EXTENSIONS,
    PDF_MEDIA_TYPE,
    PNG_MEDIA_TYPE,
    PPTX_MEDIA_TYPE,
    SUPPORTED_EXTENSIONS,
    UnsupportedContent,
    accepted_formats,
    detect_media_type,
)


@pytest.mark.parametrize(
    ("prefix", "filename", "expected"),
    [
        (b"%PDF-1.7\n%\xe2\xe3\xcf\xd3", "paper.pdf", PDF_MEDIA_TYPE),
        (b"PK\x03\x04\x14\x00", "report.DOCX", DOCX_MEDIA_TYPE),
        (b"PK\x03\x04\x14\x00", "deck.pptx", PPTX_MEDIA_TYPE),
        (b"# Heading", "notes.md", "text/markdown"),
        (b"# Heading", "notes.markdown", "text/markdown"),
        (b"plain evidence", "quote.txt", "text/plain"),
        ("accented évidence".encode(), "quote.txt", "text/plain"),
    ],
)
def test_supported_formats_resolve(prefix: bytes, filename: str, expected: str) -> None:
    assert detect_media_type(prefix, filename) == expected


@pytest.mark.parametrize(
    ("prefix", "filename", "code"),
    [
        (b"%PDF-1.7", "disguised.txt", "content_mismatch"),
        (b"PK\x03\x04", "disguised.md", "content_mismatch"),
        (b"just text", "claimed.pptx", "content_mismatch"),
        (b"\xd0\xcf\x11\xe0", "legacy.ppt", "unsupported_extension"),
        (b"just text", "claimed.pdf", "content_mismatch"),
        (b"just text", "claimed.docx", "content_mismatch"),
        (b"\x00\x01\x02binary", "binary.txt", "content_mismatch"),
        (b"MZ\x90\x00", "tool.exe", "unsupported_extension"),
        (b"anything", "no-extension", "unsupported_extension"),
    ],
)
def test_mismatched_and_unknown_formats_are_rejected(
    prefix: bytes, filename: str, code: str
) -> None:
    with pytest.raises(UnsupportedContent) as raised:
        detect_media_type(prefix, filename)
    assert raised.value.code == code


PNG_PREFIX = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"


def test_an_ordinary_upload_refuses_a_png() -> None:
    """A library takes a picture only from Studio, which says so."""
    with pytest.raises(UnsupportedContent) as raised:
        detect_media_type(PNG_PREFIX, "photo.png")
    assert raised.value.code == "unsupported_extension"


def test_studio_may_store_a_png_and_only_a_real_one() -> None:
    assert detect_media_type(PNG_PREFIX, "heron.png", IMAGE_EXTENSIONS) == PNG_MEDIA_TYPE
    with pytest.raises(UnsupportedContent) as raised:
        detect_media_type(b"%PDF-1.7", "disguised.png", IMAGE_EXTENSIONS)
    assert raised.value.code == "content_mismatch"


def test_a_png_named_as_text_is_not_text() -> None:
    with pytest.raises(UnsupportedContent) as raised:
        detect_media_type(PNG_PREFIX, "disguised.txt")
    assert raised.value.code == "content_mismatch"


def test_a_prefix_cut_mid_character_is_still_text() -> None:
    """The prefix is a fixed byte count, so it can split a UTF-8 sequence."""
    truncated = "long évidence".encode()[:-1]
    assert detect_media_type(truncated, "quote.txt") == "text/plain"


def test_every_accepted_extension_has_a_name_for_it() -> None:
    """The refusal message is the only place a person learns what is accepted.

    It was written out by hand beside the table it described, and the two
    drifted: `.pptx` was added to `SUPPORTED_EXTENSIONS` and the sentence
    was not, so someone whose slide deck was named wrong was told Primer
    does not take slide decks - about the one format that had just gained
    picture OCR.
    """
    assert set(FORMAT_NAMES) == set(SUPPORTED_EXTENSIONS)


def test_the_refusal_names_every_format_and_repeats_none() -> None:
    """Markdown has two extensions and is one format to the person reading."""
    accepted = accepted_formats()

    for name in set(FORMAT_NAMES.values()):
        assert name in accepted
    assert accepted.count("Markdown") == 1


def test_a_rejected_upload_is_told_about_slide_decks() -> None:
    """The bug itself, so this file says what it is protecting."""
    with pytest.raises(UnsupportedContent) as raised:
        detect_media_type(b"anything", "deck.key")

    assert "PPTX" in raised.value.message
