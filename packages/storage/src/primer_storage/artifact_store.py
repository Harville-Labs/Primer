"""Intermediate results handed between ingestion stages.

Stages run in separate processes and communicate only through job ids, so
what one stage produces has to be durable somewhere the next can read it.
Artifacts live beside source objects in the same filesystem, keyed by
version and generation, so a rebuilt generation writes a new artifact
instead of overwriting the one a running search still depends on.

Everything here is synchronous: only workers use it, and workers have no
event loop to block.
"""

from __future__ import annotations

import json
from pathlib import PurePosixPath
from typing import Any
from uuid import UUID

import fsspec

CONVERSIONS = "conversions"


class ArtifactStore:
    """Reads and writes per-generation stage output.

    It also keeps conversions, which are the exception to keying by
    generation: a conversion depends only on the source bytes and how they
    were read, so it is keyed by the source's hash and shared by every
    version one owner holds of the same content.
    """

    def __init__(self, url: str, *, prefix: str = "artifacts") -> None:
        self._fs, self._root = fsspec.core.url_to_fs(url)
        self._prefix = prefix

    def key(self, version_id: UUID, generation_id: UUID, name: str) -> str:
        return str(
            PurePosixPath(self._root, self._prefix, str(version_id), str(generation_id), name)
        )

    def write_json(self, version_id: UUID, generation_id: UUID, name: str, payload: Any) -> str:
        key = self.key(version_id, generation_id, name)
        self._fs.makedirs(str(PurePosixPath(key).parent), exist_ok=True)
        with self._fs.open(key, "wb") as handle:
            handle.write(json.dumps(payload, separators=(",", ":")).encode())
        return key

    def read_json(self, version_id: UUID, generation_id: UUID, name: str) -> Any:
        with self._fs.open(self.key(version_id, generation_id, name), "rb") as handle:
            return json.loads(handle.read())

    def exists(self, version_id: UUID, generation_id: UUID, name: str) -> bool:
        return bool(self._fs.exists(self.key(version_id, generation_id, name)))

    def discard_generation(self, version_id: UUID, generation_id: UUID) -> None:
        """Remove everything a generation produced, once it is no longer needed."""
        directory = str(
            PurePosixPath(self._root, self._prefix, str(version_id), str(generation_id))
        )
        try:
            self._fs.rm(directory, recursive=True)
        except FileNotFoundError:
            return

    def _conversions(self, sha256: str, owner: UUID | None = None) -> PurePosixPath:
        directory = PurePosixPath(self._root, CONVERSIONS, sha256)
        return directory if owner is None else directory / str(owner)

    def read_conversion(self, sha256: str, owner: UUID, made_with: str) -> bytes | None:
        """A stored conversion of these bytes, or None if there is none yet."""
        try:
            with self._fs.open(
                str(self._conversions(sha256, owner) / f"{made_with}.json"), "rb"
            ) as f:
                return f.read()
        except FileNotFoundError:
            return None

    def write_conversion(self, sha256: str, owner: UUID, made_with: str, payload: bytes) -> None:
        directory = self._conversions(sha256, owner)
        self._fs.makedirs(str(directory), exist_ok=True)
        with self._fs.open(str(directory / f"{made_with}.json"), "wb") as handle:
            handle.write(payload)

    def discard_conversions(self, sha256: str, owner: UUID | None = None) -> None:
        """Remove one owner's conversions of a source, or everyone's."""
        try:
            self._fs.rm(str(self._conversions(sha256, owner)), recursive=True)
        except FileNotFoundError:
            return
