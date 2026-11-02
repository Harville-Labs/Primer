"""Pictures from Studio, read as documents through a vision model.

The model is faked: these tests are about what Primer sends it, what it
makes of the reply, and how a failure is reported - not about how well a
model sees.
"""

from __future__ import annotations

import io
import uuid
from pathlib import Path
from typing import Any

import httpx2
import openai
import pytest
from haystack.dataclasses import ChatMessage, ImageContent
from PIL import Image
from PIL.PngImagePlugin import PngInfo
from primer_contracts.studio import PNG_MODEL_KEYWORD, PNG_PROMPT_KEYWORD
from primer_ingestion.chunking import DocumentContext
from primer_ingestion.config import Settings
from primer_ingestion.errors import PermanentStageError, StageError, UnsupportedDocument
from primer_ingestion.images import ModelCaptioner, build_captioner, conversion_key
from primer_ingestion.parsing import DocumentParser

PNG = "image/png"


def with_vision() -> Settings:
    return Settings(vision_base_url="http://vision:8000/v1", vision_model="Qwen/Qwen2.5-VL-7B")


def png(*, prompt: str | None = None, model: str | None = None) -> bytes:
    info = PngInfo()
    if prompt:
        info.add_itxt(PNG_PROMPT_KEYWORD, prompt)
    if model:
        info.add_itxt(PNG_MODEL_KEYWORD, model)
    out = io.BytesIO()
    Image.new("RGB", (8, 6), (40, 90, 160)).save(out, format="PNG", pnginfo=info)
    return out.getvalue()


CONTEXT = DocumentContext(
    owner_user_id=uuid.uuid5(uuid.NAMESPACE_URL, "owner"),
    library_id=uuid.uuid5(uuid.NAMESPACE_URL, "library"),
    document_id=uuid.uuid5(uuid.NAMESPACE_URL, "document"),
    document_version_id=uuid.uuid5(uuid.NAMESPACE_URL, "version"),
    generation_id=uuid.uuid5(uuid.NAMESPACE_URL, "generation"),
    filename="primer-image.png",
)


def parse(tmp_path: Path, data: bytes, **settings: Any) -> list[str]:
    source = tmp_path / "source"
    source.write_bytes(data)
    parser = DocumentParser(
        Settings(chunk_tokenizer=None, enable_ocr=False, **settings),
        captioner=lambda _: "A heron standing in shallow water at dawn.",
    )
    chunks = parser.parse_and_chunk(source, CONTEXT, media_type=PNG)
    return [chunk.content for chunk in chunks]


def test_an_image_is_found_by_its_prompt_and_what_it_shows(tmp_path: Path) -> None:
    text = " ".join(parse(tmp_path, png(prompt="A heron in fog", model="flux")))

    assert "A heron in fog" in text
    assert "flux" in text
    assert "heron standing in shallow water" in text


def test_an_image_without_a_recipe_is_still_described(tmp_path: Path) -> None:
    """An upload that was only edited in Studio has no prompt of its own."""
    text = " ".join(parse(tmp_path, png()))

    assert "heron standing in shallow water" in text
    assert "prompt" not in text


def test_without_a_vision_model_an_image_is_refused_for_good() -> None:
    parser = DocumentParser(Settings(chunk_tokenizer=None))

    with pytest.raises(UnsupportedDocument) as raised:
        parser.conversion_key(PNG)

    assert raised.value.code == "vision_unconfigured"
    assert "PRIMER_VISION_MODEL" in (raised.value.detail or "")


def test_a_description_is_reused_only_by_the_model_that_wrote_it() -> None:
    """A conversion key becomes a file name, and model names carry slashes."""
    first = conversion_key("Qwen/Qwen2.5-VL-7B")

    assert "/" not in first
    assert first != conversion_key("llava")
    assert first == DocumentParser(with_vision(), captioner=str).conversion_key(PNG)


class FakeVision:
    """Stands in for the generator's `run`, recording what it was sent."""

    def __init__(self, reply: str | Exception) -> None:
        self.reply = reply
        self.sent: list[list[ChatMessage]] = []

    def __call__(self, messages: list[ChatMessage]) -> dict[str, Any]:
        self.sent.append(messages)
        if isinstance(self.reply, Exception):
            raise self.reply
        return {"replies": [ChatMessage.from_assistant(self.reply)]}


def captioner_with(vision: FakeVision) -> ModelCaptioner:
    return ModelCaptioner(vision)


def test_the_image_itself_is_what_the_model_is_sent() -> None:
    vision = FakeVision("  A heron.  ")

    description = captioner_with(vision)(png())

    assert description == "A heron."
    images = [part for part in vision.sent[0][-1].images if isinstance(part, ImageContent)]
    assert len(images) == 1
    assert images[0].mime_type == PNG


def failure(status: int) -> openai.APIStatusError:
    response = httpx2.Response(status, request=httpx2.Request("POST", "http://vision/v1/chat"))
    return openai.APIStatusError("refused", response=response, body=None)


def test_a_vision_model_that_refuses_the_request_is_not_retried() -> None:
    caption = captioner_with(FakeVision(failure(404)))

    with pytest.raises(PermanentStageError) as raised:
        caption(png())

    assert raised.value.code == "vision_rejected"
    assert "PRIMER_VISION_MODEL" in (raised.value.detail or "")


@pytest.mark.parametrize("reply", [failure(503), ""])
def test_a_vision_model_that_is_down_or_says_nothing_is_retried(reply: str | Exception) -> None:
    caption = captioner_with(FakeVision(reply))

    with pytest.raises(StageError) as raised:
        caption(png())

    assert type(raised.value) is StageError


def test_vision_is_off_unless_both_endpoint_and_model_are_set() -> None:
    assert build_captioner(Settings(vision_base_url="http://vision:8000/v1")) is None
    assert build_captioner(Settings(vision_model="llava")) is None
    assert build_captioner(with_vision()) is not None
