"""The storage Primer speaks to, and nothing else.

Two schemes: `file://` for a directory, and `s3://` for any S3-compatible
object store. A NAS, a cluster file system, or a cloud disk is mounted by
the operating system or a CSI driver and reached as `file://`;
docs/adr/0016 says why Primer does not speak SMB, NFS, or WebDAV itself.

fsspec would accept many more schemes, which is why this is an allow-list
rather than a pass-through: every scheme it knows is one an operator could
type, and most would half-work - a backend that is not installed fails with
an ImportError on the first upload, and one that is installed has never been
run against Primer's rename-into-place writes.
"""

from __future__ import annotations

import importlib.util
from contextlib import suppress
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import fsspec

#: Scheme to the module that implements it, which fsspec imports lazily.
SUPPORTED_SCHEMES = {"file": None, "s3": "s3fs"}

PROBE_DIRECTORY = ".primer-probe"


class UnsupportedStorage(ValueError):
    """A storage URL Primer cannot use, said in terms of what to change."""


def check_url(url: str) -> str:
    """Return `url` if Primer can store through it, or say why it cannot.

    Called where configuration is read, so a mistyped scheme stops a service
    from starting rather than failing someone's first upload.
    """
    scheme = urlsplit(url).scheme
    if scheme not in SUPPORTED_SCHEMES:
        named = f"`{scheme}://`" if scheme else "a URL with no scheme"
        raise UnsupportedStorage(
            f"PRIMER_SOURCE_STORE_URL is {named}; Primer stores through "
            "`file://` (a directory, which may be a mounted NAS share) or `s3://` "
            "(any S3-compatible object store)."
        )
    module = SUPPORTED_SCHEMES[scheme]
    if module is not None and importlib.util.find_spec(module) is None:
        raise UnsupportedStorage(
            f"PRIMER_SOURCE_STORE_URL is `{scheme}://`, which needs the {module} "
            "package, and this image does not have it."
        )
    return url


def open_filesystem(url: str) -> tuple[Any, str]:
    """The filesystem and root path behind a checked storage URL."""
    return fsspec.core.url_to_fs(check_url(url))


def probe(fs: Any, root: str) -> bool:
    """Write, read back, and delete a small object under `root`.

    Proves the store is reachable and writable with the credentials this
    process has, which is what an upload needs; listing or checking that the
    root exists would pass on a read-only mount. Raises on failure, as the
    readiness registry expects of a probe that has something to say.
    """
    directory = f"{root.rstrip('/')}/{PROBE_DIRECTORY}"
    key = f"{directory}/{uuid4().hex}"
    payload = uuid4().bytes
    fs.makedirs(directory, exist_ok=True)
    try:
        with fs.open(key, "wb") as handle:
            handle.write(payload)
        with fs.open(key, "rb") as handle:
            return handle.read() == payload
    finally:
        # A write that failed left nothing to delete, and its error is the
        # one worth reporting.
        with suppress(FileNotFoundError):
            fs.rm_file(key)
