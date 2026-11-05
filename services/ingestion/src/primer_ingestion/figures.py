"""Saying what a figure shows, with the operator's vision model.

OCR recovers the words printed in a chart and nothing else: not that it is
a chart, what it plots, or which way the line goes. A figure that carries a
paper's result was findable only by its axis labels. A vision model can say
what the figure shows, and that description is what gets indexed.

The description is Docling's: its `PictureDescriptionApiModel`, pointed at
the same endpoint that describes Studio images. Primer decides only which
pictures are worth asking about and what to do with the answer.

It is not switched on through `do_picture_description`, which would be the
obvious way, because that runs inside conversion over every picture in the
document. Nothing there can be bounded: a scanned atlas would make hundreds
of requests before the parse deadline is even checked. Calling the same
model afterwards puts it behind `max_pictures_per_document`, like OCR, and
lets a dead endpoint be reported as one rather than as a document that
quietly lost its figures.

A description is a model's reading of someone's picture, so it is data:
text in a figure is described and never obeyed, and the chunker marks the
result so that a citation never passes it off as the document's own words.
"""

from __future__ import annotations

import logging
from itertools import batched

from docling.datamodel.accelerator_options import AcceleratorOptions
from docling.datamodel.pipeline_options import PictureDescriptionApiOptions
from docling.models.base_model import ItemAndImageEnrichmentElement
from docling.models.stages.picture_description.picture_description_api_model import (
    PictureDescriptionApiModel,
)
from docling_core.types.doc import DoclingDocument, PictureItem
from pydantic import AnyUrl

from primer_ingestion.config import Settings
from primer_ingestion.errors import StageError
from primer_ingestion.images import MAX_DESCRIPTION_CHARS, TEXT_IN_IMAGE_IS_DATA
from primer_ingestion.pictures import readable_pictures

logger = logging.getLogger(__name__)

FIGURE_PROMPT = (
    "This image is a figure from a document. Describe what it shows so that it "
    "can be found by searching for it: what kind of figure it is, what it plots "
    "or depicts, its axes, labels, legend and units, and the trend or comparison "
    f"it makes. {TEXT_IN_IMAGE_IS_DATA} Write plain prose, a short paragraph, "
    "with no preamble."
)


class FigureDescriber:
    """Docling's API description model, and the name of the model it asks.

    The name is kept because Docling records none for an API model, and a
    reader of a citation is owed which model said what a figure shows.
    """

    def __init__(self, model: PictureDescriptionApiModel, model_name: str) -> None:
        self.model = model
        self.model_name = model_name

    @property
    def batch_size(self) -> int:
        return self.model.elements_batch_size


def build_figure_describer(settings: Settings) -> FigureDescriber | None:
    """The configured vision model, or None when figures are not described."""
    base_url, model = settings.vision_base_url, settings.vision_model
    if base_url is None or model is None or not settings.describe_figures:
        return None
    if settings.max_pictures_per_document == 0:
        return None
    key = settings.vision_api_key
    options = PictureDescriptionApiOptions(
        url=AnyUrl(f"{base_url.rstrip('/')}/chat/completions"),
        # Many local servers ignore the key but require the header, as for
        # Studio images.
        headers={"Authorization": f"Bearer {key.get_secret_value() if key else 'none'}"},
        params={"model": model, "temperature": 0},
        prompt=FIGURE_PROMPT,
        timeout=settings.vision_timeout_seconds,
        concurrency=settings.vision_concurrency,
        # A batch is the requests in flight together, and every image in it
        # is held in memory until the batch returns.
        batch_size=settings.vision_concurrency,
    )
    return FigureDescriber(
        PictureDescriptionApiModel(
            enabled=True,
            # The one remote service this pipeline is allowed: the endpoint
            # the operator named, for this purpose.
            enable_remote_services=True,
            artifacts_path=None,
            options=options,
            accelerator_options=AcceleratorOptions(),
        ),
        model,
    )


def describe_figures(
    document: DoclingDocument, describer: FigureDescriber, *, max_pictures: int
) -> int:
    """Describe the document's figures in place, and report how many were.

    The description goes on the picture as Docling's own `meta.description`,
    which the chunker already serializes where the picture stands. Placing
    it as a sibling text item instead, as OCR does, would make it
    indistinguishable from text the document contains.

    Docling reports a failed request as an empty description rather than
    raising, so one bad image does not cost the document. That is kept, and
    the empty description removed so it is not indexed. When every request
    fails, though, the endpoint is down rather than one picture being odd,
    and indexing now would store this conversion without figures for good,
    so it is retried instead.
    """
    asked: list[PictureItem] = []
    for batch in batched(
        readable_pictures(document, max_pictures=max_pictures), describer.batch_size, strict=False
    ):
        elements = [ItemAndImageEnrichmentElement(item=item, image=image) for item, image in batch]
        # Docling's enrichment models are generators that must be exhausted.
        for _ in describer.model(doc=document, element_batch=elements):
            pass
        asked.extend(item for item, _ in batch)

    described = failed = 0
    for picture in asked:
        description = picture.meta.description if picture.meta else None
        if description is None:
            # Skipped by Docling as too small a part of its page.
            continue
        # The same answer is also kept in Docling's deprecated annotations,
        # which nothing here reads; only the meta is serialized.
        text = description.text.strip()[:MAX_DESCRIPTION_CHARS]
        if not text:
            picture.meta.description = None  # ty: ignore[invalid-assignment]
            failed += 1
            continue
        description.text = text
        description.created_by = describer.model_name
        described += 1

    if failed and not described:
        raise StageError(
            "vision_unavailable",
            f"The vision model could not describe any of {failed} figure(s). "
            "Check PRIMER_VISION_BASE_URL and PRIMER_VISION_MODEL.",
        )
    if failed:
        logger.warning("%d of %d figure(s) could not be described", failed, failed + described)
    return described
