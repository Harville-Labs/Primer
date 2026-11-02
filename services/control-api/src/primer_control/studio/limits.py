"""Studio's refusals, worded once for the upload and generation routes.

The quota and the switch are checked in more than one place: before a
generation is started, and again before its results are stored, because a
generation can take minutes and an upload can land while it runs. Each
refusal is built here so the two checks cannot word or code it differently.
"""

from __future__ import annotations

from fastapi import status
from primer_contracts.errors import ErrorCode
from primer_service.errors import ProblemError

from primer_control.config import Settings


def not_found() -> ProblemError:
    return ProblemError(
        code=ErrorCode.NOT_FOUND,
        title="Image not found",
        status_code=status.HTTP_404_NOT_FOUND,
        detail="No Studio image with that identifier is available to you.",
    )


def require_studio(settings: Settings) -> None:
    """404 rather than 403: a deployment without Studio has no Studio routes."""
    if not settings.studio_enabled:
        raise ProblemError(
            code=ErrorCode.NOT_FOUND,
            title="Studio is off",
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This deployment has not turned Studio on.",
        )


def require_studio_library(settings: Settings) -> None:
    """404, like Studio itself: without a vision model there is no such route.

    A picture added to a library with nothing to read it would be a document
    that never finds anything, and failing it at ingestion is failing it
    after someone has been told it was added.
    """
    if not settings.vision_enabled:
        raise ProblemError(
            code=ErrorCode.NOT_FOUND,
            title="Images cannot be added to libraries",
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This deployment has no vision model to read images added to a library.",
        )


def over_quota(used: int, adding: int, settings: Settings) -> ProblemError | None:
    """The refusal for storing `adding` more bytes, or None when they fit."""
    if used + adding <= settings.studio_quota_bytes:
        return None
    return ProblemError(
        code=ErrorCode.QUOTA_EXCEEDED,
        title="Studio storage full",
        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
        detail=(
            f"Your Studio images use {used} of {settings.studio_quota_bytes} bytes. "
            "Delete some to make room."
        ),
    )


def upload_too_large(settings: Settings) -> ProblemError:
    return ProblemError(
        code=ErrorCode.QUOTA_EXCEEDED,
        title="Upload too large",
        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
        detail=f"Studio takes images up to {settings.studio_max_upload_bytes} bytes.",
    )
