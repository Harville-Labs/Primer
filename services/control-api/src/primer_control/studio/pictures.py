"""Deciding what an image is, and keeping only the pixels.

Everything Studio stores passes through `normalized`, whether a person
uploaded it or a provider made it. Both are outside Primer: a phone photo
carries the place it was taken in its EXIF, and a backend is the operator's
choice but still answers with whatever bytes it likes. Decoding and
re-encoding is what makes the stored file a picture Primer wrote rather than
a file someone else did.

The size is read from the header before any pixels are decoded. A small file
can declare an enormous image, and refusing it after decoding is refusing it
after the memory has been spent.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from fastapi import status
from PIL import Image, UnidentifiedImageError
from PIL.PngImagePlugin import PngInfo
from primer_contracts.errors import ErrorCode
from primer_contracts.studio import PNG_MODEL_KEYWORD, PNG_PROMPT_KEYWORD
from primer_service.errors import ProblemError

#: What a person may upload. A provider's output is held to the same list:
#: every API Primer speaks returns one of these.
ACCEPTED_FORMATS = frozenset({"PNG", "JPEG", "WEBP"})


@dataclass(frozen=True, slots=True)
class Picture:
    """An image as Studio stores it: PNG, no metadata."""

    data: bytes
    width: int
    height: int


def unsupported(detail: str) -> ProblemError:
    return ProblemError(
        code=ErrorCode.UNSUPPORTED_CONTENT,
        title="Not an image Studio can use",
        status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        detail=detail,
    )


def normalized(data: bytes, *, max_side: int) -> Picture:
    """Decode an image and write it again as a PNG with nothing but pixels.

    Raises a 415 for anything that is not a PNG, JPEG or WEBP, or is wider
    or taller than `max_side`. The caller says in its own words where the
    bytes came from when that matters; this only says what was wrong.
    """
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in ACCEPTED_FORMATS:
                raise unsupported("Upload a PNG, JPEG or WEBP image.")
            width, height = image.size
            if width > max_side or height > max_side:
                raise unsupported(
                    f"The image is {width}x{height}; Studio takes images up to "
                    f"{max_side} pixels on a side."
                )
            image.load()
            # Only the first frame of an animated WEBP. Studio edits stills,
            # and a gallery of them should not be quietly moving.
            converted = image.convert("RGBA" if _has_alpha(image) else "RGB")
    except ProblemError:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError) as error:
        raise unsupported("The file is not an image Studio can read.") from error

    out = io.BytesIO()
    # A fresh image from `convert` carries no `info`, and nothing is passed
    # to `save` but the format: no EXIF, no ICC profile, no text chunks.
    converted.save(out, format="PNG", optimize=False)
    return Picture(data=out.getvalue(), width=converted.width, height=converted.height)


def _has_alpha(image: Image.Image) -> bool:
    return image.mode in {"RGBA", "LA", "PA"} or (
        image.mode == "P" and "transparency" in image.info
    )


def with_provenance(data: bytes, *, prompt: str | None, model: str | None) -> bytes:
    """The stored PNG again, carrying the prompt and model that made it.

    A copy leaving Studio for a library takes its recipe with it, because
    the library keeps no link back: the Studio image can be deleted and the
    document must still say what it is. Written as text chunks rather than
    a second record so ingestion reads it from the bytes it was given.

    Only Primer's own words go in. The bytes were written by `normalized`,
    so there is nothing else in the file to carry over.
    """
    info = PngInfo()
    if prompt:
        info.add_itxt(PNG_PROMPT_KEYWORD, prompt)
    if model:
        info.add_itxt(PNG_MODEL_KEYWORD, model)
    with Image.open(io.BytesIO(data)) as image:
        out = io.BytesIO()
        image.save(out, format="PNG", pnginfo=info)
    return out.getvalue()
