"""How a document is split, which is what a citation ends up quoting.

`HybridChunker` needs the embedding model's tokenizer. Without one, chunking
falls back to `HierarchicalChunker`, which splits on document structure and
merges nothing, so a document whose layout is many short items is stored one
line at a time. Both are real chunkers and both produce chunks, so the
fallback looks like a working install right up until someone reads a
citation and finds two words in it. These pin the two things that make the
difference visible - a warning when it happens, and a refusal when a
tokenizer was configured but is wrong.
"""

from __future__ import annotations

import logging

import pytest
from docling.chunking import HierarchicalChunker
from docling_core.transforms.chunker import BaseChunker
from primer_contracts.retrieval import SourceLocator
from primer_ingestion import chunking
from primer_ingestion.chunking import Passage, _merge_fragments, build_chunker
from primer_ingestion.config import Settings


def settings(*, chunk_tokenizer: str | None = None, max_chunk_tokens: int = 512) -> Settings:
    """The two settings these tests vary, named rather than splatted.

    `**overrides` needed a suppression to type-check, and the one it
    carried was mypy's spelling - so it silenced nothing and the checker
    reported the call site once per field on the model.
    """
    return Settings(
        broker_url="memory://",
        chunk_tokenizer=chunk_tokenizer,
        max_chunk_tokens=max_chunk_tokens,
    )


def test_structural_chunking_announces_itself(caplog: pytest.LogCaptureFixture) -> None:
    """Silent, this is a bug whose symptom points nowhere near its cause.

    Someone reading "citations quote one word" has no reason to look at an
    unset environment variable, so the worker has to say so itself.
    """
    with caplog.at_level(logging.WARNING):
        chunker = build_chunker(settings())

    assert isinstance(chunker, HierarchicalChunker)
    # The variable is named, because the message is the whole fix.
    assert "PRIMER_CHUNK_TOKENIZER" in caplog.text


def test_a_configured_tokenizer_is_not_quietly_abandoned(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Falling back here would answer a misconfiguration by doing the exact
    thing the setting exists to prevent, and would do it silently.

    This raises during worker startup, so the deployment crash-loops with a
    legible reason rather than failing documents one at a time.
    """

    def refuse(*_: object, **__: object) -> object:
        raise OSError("not a known repository")

    monkeypatch.setattr(chunking.HuggingFaceTokenizer, "from_pretrained", refuse)

    with caplog.at_level(logging.WARNING), pytest.raises(RuntimeError) as raised:
        build_chunker(settings(chunk_tokenizer="nobody/not-a-real-model"))

    assert "nobody/not-a-real-model" in str(raised.value)
    assert "PRIMER_CHUNK_TOKENIZER" not in caplog.text


def test_a_tokenizer_bounds_chunks_by_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    """The configured ceiling has to reach the tokenizer, not just be read.

    Loading a real tokenizer would download one, so what is checked is the
    argument: `max_tokens` is what the chunker measures against, and a
    default silently used in place of the configured value would size every
    chunk in the deployment wrongly.
    """
    tokenizer_args: dict[str, object] = {}
    chunker_args: dict[str, object] = {}

    def record_tokenizer(name: str, **kwargs: object) -> object:
        tokenizer_args["name"] = name
        tokenizer_args.update(kwargs)
        return object()

    def record_chunker(**kwargs: object) -> BaseChunker:
        chunker_args.update(kwargs)
        # A real chunker, because `build_chunker` promises to return one and
        # a test that let it return a dict would be checking a shape the
        # caller can never receive.
        return HierarchicalChunker()

    monkeypatch.setattr(chunking.HuggingFaceTokenizer, "from_pretrained", record_tokenizer)
    monkeypatch.setattr(chunking, "HybridChunker", record_chunker)

    build_chunker(settings(chunk_tokenizer="BAAI/bge-m3", max_chunk_tokens=1024))

    assert tokenizer_args == {"name": "BAAI/bge-m3", "max_tokens": 1024}
    # Peers merged, which is the half of the fix that makes small pieces
    # into passages rather than only capping large ones.
    assert chunker_args["merge_peers"] is True


class TestJoiningFragments:
    """Passages too short to retrieve on, and what becomes of them.

    `merge_peers` only merges chunks that share a heading, so it does
    nothing for the shape that produces the worst fragments: a form or a
    statement whose labels are recognised as headings, leaving every value
    alone under one of its own. The result is an index of bare numbers.
    """

    def passage(self, content: str, *, heading: str = "", page: int | None = None) -> Passage:
        return Passage(
            content=content,
            embedding_text=f"{heading}\n{content}" if heading else content,
            locator=SourceLocator(page=page, section=heading or None),
        )

    def join(self, passages: list[Passage], *, min_chars: int = 40) -> list[Passage]:
        return _merge_fragments(passages, size=len, budget=10_000, min_chars=min_chars)

    def test_a_bare_value_is_joined_to_its_neighbours(self) -> None:
        """The measured case: a W2 read as one chunk per box."""
        joined = self.join(
            [
                self.passage("52,000.00", heading="Wages, tips, other compensation"),
                self.passage("6,240.00", heading="Federal income tax withheld"),
                self.passage("Homeowners", heading="Employee"),
            ]
        )

        assert len(joined) == 1
        assert joined[0].content == "52,000.00\n6,240.00\nHomeowners"

    def test_the_headings_survive_into_the_embedding_text(self) -> None:
        """Without them the passage is a column of numbers.

        The heading is the only thing saying which number is a wage and
        which a withholding, and it lives in a different passage from the
        number, so a join that kept only the contents would embed text that
        answers nothing.
        """
        joined = self.join(
            [
                self.passage("52,000.00", heading="Wages, tips, other compensation"),
                self.passage("6,240.00", heading="Federal income tax withheld"),
            ]
        )

        assert "Wages, tips, other compensation" in joined[0].embedding_text
        assert "Federal income tax withheld" in joined[0].embedding_text

    def test_the_content_stays_quotable_from_the_source(self) -> None:
        """A citation has to be findable in the document it points at.

        `contextualize` inserts headings around a chunk rather than
        reproducing the source, so promoting it into the content would
        quote the document words it does not contain. Each joined piece is
        still its own verbatim text.
        """
        joined = self.join(
            [
                self.passage("52,000.00", heading="Wages, tips, other compensation"),
                self.passage("6,240.00", heading="Federal income tax withheld"),
            ]
        )

        assert joined[0].content == "52,000.00\n6,240.00"

    def test_ordinary_prose_is_left_alone(self) -> None:
        """The threshold has to be far below a sentence.

        Set anywhere near ordinary prose this does real damage: at 200 it
        merged a two-section paper into a single chunk and took its section
        locators with it. Passages long enough to retrieve on are not
        touched.
        """
        passages = [
            self.passage("Grounding answers in cited sources reduces unsupported claims."),
            self.passage("Recall at rank ten was the decisive metric for this corpus."),
        ]

        assert self.join(passages) == passages

    def test_nothing_is_joined_across_a_page(self) -> None:
        """A passage carries one page number, so a join across two lies.

        Slides are the sharp case - one page each - and a citation to a
        joined pair would send the reader to a slide holding half of what
        they were shown.
        """
        joined = self.join(
            [self.passage("Revenue", page=1), self.passage("Up 12%", page=2)],
        )

        assert [passage.content for passage in joined] == ["Revenue", "Up 12%"]

    def test_a_join_stays_inside_the_token_budget(self) -> None:
        """The chunker's budget is the embedding model's; a join cannot exceed it."""
        joined = _merge_fragments(
            [self.passage("short"), self.passage("also short"), self.passage("third")],
            size=len,
            budget=12,
            min_chars=40,
        )

        assert len(joined) > 1

    def test_joining_can_be_switched_off(self) -> None:
        passages = [self.passage("52,000.00"), self.passage("6,240.00")]

        assert self.join(passages, min_chars=0) == passages


def test_the_tokenizer_is_derived_from_the_embedding_model() -> None:
    """Ollama names a model `nomic-embed-text`, which is not a repository.

    The worker is told the model Retrieval embeds with and finds its
    tokenizer itself, so the two cannot be configured out of step.
    """
    derived = Settings(broker_url="memory://", embedding_model="nomic-embed-text")

    assert derived.chunk_tokenizer == "nomic-ai/nomic-embed-text-v1.5"


def test_a_named_tokenizer_wins_over_the_derived_one() -> None:
    """The way out for a hosted model, which publishes none to derive."""
    named = Settings(
        broker_url="memory://",
        embedding_model="text-embedding-3-small",
        chunk_tokenizer="BAAI/bge-m3",
    )

    assert named.chunk_tokenizer == "BAAI/bge-m3"


def test_a_hosted_model_leaves_chunking_on_structure() -> None:
    """Guessing a repository would stop the worker starting at all."""
    hosted = Settings(broker_url="memory://", embedding_model="text-embedding-3-small")

    assert hosted.chunk_tokenizer is None


def test_chunks_longer_than_the_model_reads_are_refused() -> None:
    """The server truncates silently, so the end of every long chunk would
    be stored, quoted, and never searchable."""
    with pytest.raises(ValueError, match="PRIMER_MAX_CHUNK_TOKENS"):
        Settings(
            broker_url="memory://",
            embedding_model="BAAI/bge-small-en-v1.5",
            max_chunk_tokens=1024,
        )
