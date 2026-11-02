"""Content-addressed storage for uploaded source bytes.

Control writes source objects and workers read them. Sharing one
implementation means both agree on how a hash maps to a stored object, which
is the whole basis of Primer's deduplication.
"""

from primer_storage.artifact_store import ArtifactStore
from primer_storage.source_store import (
    DOCX_MEDIA_TYPE,
    FORMAT_NAMES,
    IMAGE_EXTENSIONS,
    PDF_MEDIA_TYPE,
    PNG_MEDIA_TYPE,
    PPTX_MEDIA_TYPE,
    SUPPORTED_EXTENSIONS,
    QuotaExceeded,
    SourceStore,
    SourceStoreError,
    StoredSource,
    UnsupportedContent,
    accepted_formats,
    detect_media_type,
)
from primer_storage.studio_store import StudioStore

__all__ = [
    "DOCX_MEDIA_TYPE",
    "FORMAT_NAMES",
    "IMAGE_EXTENSIONS",
    "PDF_MEDIA_TYPE",
    "PNG_MEDIA_TYPE",
    "PPTX_MEDIA_TYPE",
    "SUPPORTED_EXTENSIONS",
    "ArtifactStore",
    "QuotaExceeded",
    "SourceStore",
    "SourceStoreError",
    "StoredSource",
    "StudioStore",
    "UnsupportedContent",
    "accepted_formats",
    "detect_media_type",
]

__version__ = "0.1.0"
