"""The parse stage: reading a stored source and handing chunks onward."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, cast

import pytest
from docling.document_converter import DocumentConverter
from primer_contracts.ingestion import JobClaim, StageName
from primer_ingestion.config import Settings
from primer_ingestion.errors import StageError
from primer_ingestion.parsing import DocumentParser
from primer_ingestion.stages.parse import CHUNKS_ARTIFACT, ParseStage
from primer_storage import ArtifactStore, SourceStore

MARKDOWN = b"# Findings\n\nThe corpus was small but the effect was consistent.\n"
CONTENT_HASH = "b" * 64


@pytest.fixture
def store_root(tmp_path: Path) -> Path:
    return tmp_path / "store"


@pytest.fixture
def stored_source(store_root: Path) -> str:
    """A source object, laid out the way Control writes one."""
    sources = store_root / "sources"
    sources.mkdir(parents=True)
    (sources / CONTENT_HASH).write_bytes(MARKDOWN)
    return CONTENT_HASH


@pytest.fixture
def claim() -> JobClaim:
    return JobClaim(
        job_id=uuid.uuid5(uuid.NAMESPACE_URL, "job"),
        stage=StageName.PARSE,
        generation_id=uuid.uuid5(uuid.NAMESPACE_URL, "generation"),
        attempt=1,
        document_id=uuid.uuid5(uuid.NAMESPACE_URL, "document"),
        document_version_id=uuid.uuid5(uuid.NAMESPACE_URL, "version"),
        library_id=uuid.uuid5(uuid.NAMESPACE_URL, "library"),
        owner_user_id=uuid.uuid5(uuid.NAMESPACE_URL, "owner"),
        source_sha256=CONTENT_HASH,
        filename="findings.md",
        media_type="text/markdown",
        byte_size=len(MARKDOWN),
    )


@pytest.fixture
def stage(store_root: Path) -> ParseStage:
    settings = Settings(chunk_tokenizer=None, source_store_url=f"file://{store_root}")
    return ParseStage(
        settings,
        parser=DocumentParser(settings),
        sources=SourceStore(f"file://{store_root}", max_bytes=1024 * 1024),
        artifacts=ArtifactStore(f"file://{store_root}"),
    )


def test_chunks_are_written_where_the_next_stage_reads_them(
    stage: ParseStage, claim: JobClaim, stored_source: str, store_root: Path
) -> None:
    """Stages share only a job id, so the handoff has to be durable."""
    stage(claim)

    artifact = (
        store_root
        / "artifacts"
        / str(claim.document_version_id)
        / str(claim.generation_id)
        / CHUNKS_ARTIFACT
    )
    chunks = json.loads(artifact.read_text())

    assert chunks
    assert all(chunk["library_id"] == str(claim.library_id) for chunk in chunks)
    assert all(chunk["generation_id"] == str(claim.generation_id) for chunk in chunks)
    assert any("effect was consistent" in chunk["content"] for chunk in chunks)


def test_a_rebuild_does_not_overwrite_the_generation_being_searched(
    stage: ParseStage, claim: JobClaim, stored_source: str, store_root: Path
) -> None:
    """Keying artifacts by generation is what makes a rebuild safe."""
    stage(claim)
    rebuilt = claim.model_copy(update={"generation_id": uuid.uuid4()})
    stage(rebuilt)

    generations = (store_root / "artifacts" / str(claim.document_version_id)).iterdir()
    assert {path.name for path in generations} == {
        str(claim.generation_id),
        str(rebuilt.generation_id),
    }


def test_a_missing_source_is_retryable(stage: ParseStage, claim: JobClaim) -> None:
    """Metadata says these bytes are durable, so absence is an outage, not a bad file."""
    with pytest.raises(StageError) as raised:
        stage(claim)

    assert raised.value.code == "source_unavailable"
    assert type(raised.value) is StageError


class CountingConverter:
    """Docling's converter, counting how often it is asked to run."""

    def __init__(self, settings: Settings) -> None:
        self._inner = DocumentParser(settings).converter
        self.calls = 0

    def convert(self, source: Path, **options: Any) -> Any:
        self.calls += 1
        return self._inner.convert(source, **options)


@pytest.fixture
def counting(store_root: Path) -> tuple[ParseStage, CountingConverter]:
    settings = Settings(chunk_tokenizer=None, source_store_url=f"file://{store_root}")
    converter = CountingConverter(settings)
    stage = ParseStage(
        settings,
        parser=DocumentParser(settings, converter=cast("DocumentConverter", converter)),
        sources=SourceStore(f"file://{store_root}", max_bytes=1024 * 1024),
        artifacts=ArtifactStore(f"file://{store_root}"),
    )
    return stage, converter


def read_chunks(store_root: Path, claim: JobClaim) -> list[dict[str, Any]]:
    version = store_root / "artifacts" / str(claim.document_version_id)
    return json.loads((version / str(claim.generation_id) / CHUNKS_ARTIFACT).read_text())


def another_version(claim: JobClaim) -> JobClaim:
    """The same bytes, uploaded again as a different document."""
    return claim.model_copy(
        update={
            "document_id": uuid.uuid4(),
            "document_version_id": uuid.uuid4(),
            "generation_id": uuid.uuid4(),
        }
    )


def test_the_same_bytes_are_not_converted_twice(
    counting: tuple[ParseStage, CountingConverter], claim: JobClaim, stored_source: str
) -> None:
    """Conversion is nearly all of a parse's cost, and depends only on the bytes."""
    stage, converter = counting
    stage(claim)
    stage(another_version(claim))

    assert converter.calls == 1


def test_a_reused_conversion_chunks_the_same_as_a_fresh_one(
    counting: tuple[ParseStage, CountingConverter],
    claim: JobClaim,
    stored_source: str,
    store_root: Path,
) -> None:
    """The stored document must round-trip, or reuse would quietly change answers."""
    stage, _ = counting
    stage(claim)
    again = another_version(claim)
    stage(again)

    fresh, reused = read_chunks(store_root, claim), read_chunks(store_root, again)
    scope = ("document_id", "document_version_id", "generation_id", "chunk_id")
    assert fresh
    assert [{k: v for k, v in c.items() if k not in scope} for c in reused] == [
        {k: v for k, v in c.items() if k not in scope} for c in fresh
    ]
    assert all(chunk["document_version_id"] == str(again.document_version_id) for chunk in reused)


def test_an_unreadable_stored_conversion_is_converted_again(
    counting: tuple[ParseStage, CountingConverter],
    claim: JobClaim,
    stored_source: str,
    store_root: Path,
) -> None:
    """A write cut short must cost a conversion, not fail the document."""
    stage, converter = counting
    stage(claim)
    stored = list((store_root / "conversions" / CONTENT_HASH).rglob("*.json"))
    assert stored
    for entry in stored:
        entry.write_text('{"truncated":')

    stage(another_version(claim))

    assert converter.calls == 2


def test_another_owners_identical_upload_is_converted_afresh(
    counting: tuple[ParseStage, CountingConverter], claim: JobClaim, stored_source: str
) -> None:
    """A fast parse would tell a stranger that someone else holds the same file."""
    stage, converter = counting
    stage(claim)
    stage(another_version(claim).model_copy(update={"owner_user_id": uuid.uuid4()}))

    assert converter.calls == 2
