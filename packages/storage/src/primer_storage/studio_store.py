"""Studio images, beside source objects in the same filesystem.

Keyed by image id rather than by content hash. A source object is shared
by every version that has the same bytes, which is worth a reference count
for documents that are often uploaded twice. Two Studio images with the
same bytes are rare, and keying them apart means deleting one never has to
ask whether anything else still points at it.

Bytes arrive here already decoded and re-encoded by Control, so this class
only stores them; it does not decide what an image is.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from functools import partial
from pathlib import PurePosixPath
from typing import Any
from uuid import UUID, uuid4

import fsspec
from anyio import to_thread

PREFIX = "studio"


class StudioStore:
    def __init__(self, url: str, *, chunk_bytes: int = 1024 * 1024) -> None:
        self._fs, self._root = fsspec.core.url_to_fs(url)
        self._chunk_bytes = chunk_bytes

    def _key(self, *parts: str) -> str:
        return str(PurePosixPath(self._root, PREFIX, *parts))

    async def _run(self, call: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        return await to_thread.run_sync(partial(call, *args, **kwargs))

    async def put(self, image_id: UUID, data: bytes) -> None:
        """Write an image, visible under its id only once it is complete."""
        temporary = self._key("tmp", f"{uuid4().hex}.part")
        await self._run(self._fs.makedirs, self._key("tmp"), exist_ok=True)
        try:
            await self._run(self._fs.pipe_file, temporary, data)
            await self._run(self._fs.mv, temporary, self._key(f"{image_id}.png"))
        except BaseException:
            await self.remove(image_id, key=temporary)
            raise

    async def open_stream(self, image_id: UUID) -> AsyncIterator[bytes]:
        handle = await self._run(self._fs.open, self._key(f"{image_id}.png"), "rb")
        try:
            while True:
                chunk = await self._run(handle.read, self._chunk_bytes)
                if not chunk:
                    return
                yield chunk
        finally:
            await self._run(handle.close)

    async def exists(self, image_id: UUID) -> bool:
        return bool(await self._run(self._fs.exists, self._key(f"{image_id}.png")))

    async def remove(self, image_id: UUID, *, key: str | None = None) -> None:
        """Delete an image's bytes, and say nothing if they are already gone."""
        try:
            await self._run(self._fs.rm_file, key or self._key(f"{image_id}.png"))
        except FileNotFoundError:
            return
