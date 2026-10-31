"""One forward pass at a time, and everyone told where they stand.

A model resident on one GPU runs one pass well and two badly: they compete
for memory, and the second can push the first out of it. So runs are
serialised. That also makes per-user concurrency one without a second
mechanism - nobody's runs overlap, their own included.

Not Celery. These are interactive runs measured in seconds, answered on the
request that asked; a broker would add a hop and a failure mode to deliver
what a list in this process already does.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any


class QueueFull(RuntimeError):
    def __init__(self, waiting: int) -> None:
        super().__init__(f"{waiting} runs are already waiting; try again shortly.")
        self.waiting = waiting


class RunTimedOut(RuntimeError):
    def __init__(self, seconds: float) -> None:
        super().__init__(f"The run took longer than {seconds:.0f}s and was abandoned.")
        self.seconds = seconds


#: Told how many runs are ahead each time that changes; 0 means running now.
PositionCallback = Callable[[int], Awaitable[None]]


@dataclass(eq=False)
class _Ticket:
    moved: asyncio.Event = field(default_factory=asyncio.Event)


class RunQueue:
    def __init__(self, *, max_waiting: int) -> None:
        self._max_waiting = max_waiting
        #: The head is the run in progress; everything after it is waiting.
        self._tickets: list[_Ticket] = []

    @property
    def depth(self) -> int:
        return len(self._tickets)

    async def run[T](
        self,
        work: Callable[[], T],
        *,
        limit_seconds: float,
        on_position: PositionCallback | None = None,
    ) -> T:
        """Run `work` on a thread once every run ahead of it has finished.

        A pass that overruns `limit_seconds` is abandoned, not stopped: torch has
        no way to interrupt a kernel, and killing the thread mid-call would
        leave the process unsafe to use. So the caller hears about the
        timeout at once, but the slot is held until the thread actually
        returns - releasing it early would put the next run on the GPU
        alongside the one still going, which is the thing this exists to
        prevent.
        """
        if len(self._tickets) > self._max_waiting:
            raise QueueFull(len(self._tickets) - 1)
        ticket = _Ticket()
        self._tickets.append(ticket)
        try:
            await self._wait_for_turn(ticket, on_position)
        except BaseException:
            self._leave(ticket)
            raise

        task = asyncio.ensure_future(asyncio.to_thread(work))
        task.add_done_callback(lambda done: self._finished(ticket, done))
        try:
            return await asyncio.wait_for(asyncio.shield(task), limit_seconds)
        except TimeoutError:
            raise RunTimedOut(limit_seconds) from None

    async def _wait_for_turn(self, ticket: _Ticket, on_position: PositionCallback | None) -> None:
        reported: int | None = None
        while True:
            ahead = self._tickets.index(ticket)
            if ahead != reported:
                reported = ahead
                if on_position is not None:
                    # The queue can move while this awaits; the loop reads
                    # the position again rather than trusting this one.
                    await on_position(ahead)
                    continue
            if ahead == 0:
                return
            await ticket.moved.wait()
            ticket.moved.clear()

    def _finished(self, ticket: _Ticket, done: asyncio.Future[Any]) -> None:
        # An abandoned run's failure has nobody left to receive it. Reading
        # it here marks it seen, so asyncio does not log it as lost.
        if not done.cancelled():
            done.exception()
        self._leave(ticket)

    def _leave(self, ticket: _Ticket) -> None:
        self._tickets.remove(ticket)
        for waiting in self._tickets:
            waiting.moved.set()
