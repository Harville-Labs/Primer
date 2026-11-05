"""Reading the text inside pictures that a format's own pipeline never opens.

Docling runs OCR as part of its PDF pipeline. Every other format Primer
accepts is converted by `SimplePipeline`, which has no OCR options at all -
so a slide whose content is a pasted chart, or a report whose figures carry
its numbers, converts to a title and an empty `<!-- image -->`.

That is most of what a slide deck is. A deck of screenshots was accepted,
reported ready, and indexed as almost nothing, which reads to whoever
uploaded it as Primer being unable to find text that is plainly on the
page.

The recognized text is a transcription rather than the document's own
characters, so a citation drawn from it can be subtly wrong in a way a
reader cannot see. That is the same trade the PDF pipeline already makes
and it is worth making, but it is a trade.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from typing import Any

from docling_core.types.doc import DocItemLabel, DoclingDocument, PictureItem
from PIL.Image import Image

logger = logging.getLogger(__name__)

#: Below this, on either side, there is nothing to read. Bullets, logos,
#: rules and spacer images are most of the pictures in a real document, and
#: every one of them would otherwise cost a full OCR pass to learn that.
MIN_READABLE_PIXELS = 64

#: Recognizes the text in one image. A callable rather than a class so the
#: tests can pass a function and never load an OCR model.
PictureReader = Callable[[Any], list[str]]


def build_picture_reader() -> PictureReader:
    """The OCR engine, built on first use.

    Imported inside the function on purpose. `rapidocr` pulls in
    onnxruntime, and a worker that never parses a picture should not pay
    for that at import - the same reason `DocumentParser` builds its
    converter lazily.
    """
    import numpy
    from rapidocr import RapidOCR

    engine = RapidOCR()

    def read(image: Any) -> list[str]:
        result = engine(numpy.array(image.convert("RGB")))
        return [line for line in (getattr(result, "txts", None) or []) if line.strip()]

    return read


def readable_pictures(
    document: DoclingDocument, *, max_pictures: int
) -> Iterator[tuple[PictureItem, Image]]:
    """The pictures worth a model's time, and their images, in reading order.

    Shared by OCR and by figure description so both spend the one ceiling
    the operator set on the same pictures, and skip the same bullets and
    spacers. The ceiling counts pictures, not readable ones: it bounds the
    images opened, which is the cost before any model is asked anything.
    """
    for picture in list(document.pictures or [])[:max_pictures]:
        image = picture.get_image(document)
        if image is None or min(image.width, image.height) < MIN_READABLE_PIXELS:
            continue
        yield picture, image


def read_pictures(
    document: DoclingDocument,
    read: PictureReader,
    *,
    max_pictures: int,
) -> int:
    """Add what the pictures say to the document, and report how many were read.

    The text is added as a sibling of the picture rather than as its child.
    A child of a picture is not part of the body the chunker walks: it lands
    in `document.texts`, and in nothing else - not the chunks, not even the
    markdown export - so it looks stored and is not. Placing it beside the
    picture puts it exactly where a reader would have found it.

    A picture that cannot be read is skipped rather than fatal. OCR is an
    improvement on a document that already converted, and failing the whole
    upload because one embedded image was malformed would lose the text
    that did convert.
    """
    read_count = 0
    for picture, image in readable_pictures(document, max_pictures=max_pictures):
        try:
            lines = read(image)
        except Exception:
            logger.warning("a picture could not be read", exc_info=True)
            continue
        if not lines:
            continue
        parent = picture.parent.resolve(document) if picture.parent else None
        document.add_text(label=DocItemLabel.TEXT, text=" ".join(lines), parent=parent)
        read_count += 1
    return read_count
