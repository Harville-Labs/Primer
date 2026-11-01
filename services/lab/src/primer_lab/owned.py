"""Things a user made on the resident model, held for them until it changes.

Steering vectors and probes are both computed from prompts the user typed,
which may be private, and from nothing else, so each is treated as its
maker's. They are held in memory rather than saved: one is a few kilobytes
that took seconds to make, and a saved one would have to record which model
and commit it was made on to be safe to use again. Saving them comes with
saved experiments (#174).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

#: Per owner, of each kind. The oldest goes when a new one would pass this,
#: so a session that makes them in a loop cannot grow the process without end.
MAX_PER_OWNER = 32


class Summary(Protocol):
    @property
    def id(self) -> str: ...


@dataclass(frozen=True, slots=True)
class _Held[S, V]:
    owner: UUID
    summary: S
    value: V


class Owned[S: Summary, V]:
    """Values by id, each visible only to the person who made it."""

    def __init__(self) -> None:
        self._held: dict[str, _Held[S, V]] = {}

    def add(self, owner: UUID, summary: S, value: V) -> S:
        mine = [key for key, held in self._held.items() if held.owner == owner]
        for key in mine[: max(0, len(mine) - MAX_PER_OWNER + 1)]:
            del self._held[key]
        self._held[summary.id] = _Held(owner=owner, summary=summary, value=value)
        return summary

    def listed(self, owner: UUID) -> list[S]:
        return [held.summary for held in self._held.values() if held.owner == owner]

    def owned_by(self, owner: UUID) -> dict[str, V]:
        return {key: held.value for key, held in self._held.items() if held.owner == owner}
