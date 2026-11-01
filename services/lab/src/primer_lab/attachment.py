"""Something fetched for the resident model after it loads: its SAEs, its transcoders.

Held by the resident rather than the host, so a model replaced while one of
these was downloading takes it with it, and it can never be applied to the
model that came after.
"""

from __future__ import annotations

from typing import Protocol

from primer_contracts.lab import SaeStatus, TranscoderStatus


class Fetched[Status](Protocol):
    def status(self) -> Status: ...


class Attachment[Ready: Fetched, Status: (SaeStatus, TranscoderStatus)]:
    def __init__(self, repository: str, kind: type[Status]) -> None:
        self.status: Status = kind(repository=repository, state="fetching")
        self.ready: Ready | None = None

    def attached(self, ready: Ready) -> None:
        self.ready = ready
        self.status = ready.status()

    def failed(self, message: str) -> None:
        self.status = type(self.status)(
            repository=self.status.repository, state="failed", error=message
        )
