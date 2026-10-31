"""One run at a time, with everyone told where they stand."""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Iterator

import pytest
from primer_lab.queue import QueueFull, RunQueue, RunTimedOut


@pytest.fixture
def release() -> Iterator[threading.Event]:
    """Held runs block on this. Set on teardown, so a failed assertion
    cannot leave a thread blocked and hang the test session on exit."""
    event = threading.Event()
    yield event
    event.set()


async def test_a_second_run_waits_and_hears_its_position_until_it_runs(release):
    queue = RunQueue(max_waiting=4)
    positions: list[int] = []

    async def report(ahead: int) -> None:
        positions.append(ahead)

    first = asyncio.create_task(queue.run(lambda: release.wait(5), limit_seconds=5))
    await asyncio.sleep(0.01)
    second = asyncio.create_task(queue.run(lambda: "second", limit_seconds=5, on_position=report))
    await asyncio.sleep(0.01)
    assert positions == [1]
    assert queue.depth == 2

    release.set()
    assert await second == "second"
    await first
    assert positions == [1, 0]
    assert queue.depth == 0


async def test_runs_never_overlap():
    queue = RunQueue(max_waiting=8)
    running = 0
    most = 0
    lock = threading.Lock()

    def work() -> None:
        nonlocal running, most
        with lock:
            running += 1
            most = max(most, running)
        time.sleep(0.01)
        with lock:
            running -= 1

    await asyncio.gather(*(queue.run(work, limit_seconds=5) for _ in range(5)))
    assert most == 1


async def test_a_run_beyond_the_waiting_limit_is_refused_at_once(release):
    queue = RunQueue(max_waiting=1)
    running = asyncio.create_task(queue.run(lambda: release.wait(5), limit_seconds=5))
    waiting = asyncio.create_task(queue.run(lambda: None, limit_seconds=5))
    await asyncio.sleep(0.01)
    with pytest.raises(QueueFull):
        await queue.run(lambda: None, limit_seconds=5)
    release.set()
    await asyncio.gather(running, waiting)


async def test_an_overrun_is_reported_but_holds_the_slot_until_its_thread_returns(release):
    """Releasing early would put the next run on the device beside the one
    still going, which is what the queue exists to prevent."""
    queue = RunQueue(max_waiting=4)
    with pytest.raises(RunTimedOut):
        await queue.run(lambda: release.wait(5), limit_seconds=0.05)
    assert queue.depth == 1

    after = asyncio.create_task(queue.run(lambda: "after", limit_seconds=5))
    await asyncio.sleep(0.05)
    assert not after.done()
    release.set()
    assert await after == "after"


async def test_a_waiting_run_that_is_cancelled_leaves_the_queue(release):
    queue = RunQueue(max_waiting=4)
    running = asyncio.create_task(queue.run(lambda: release.wait(5), limit_seconds=5))
    waiting = asyncio.create_task(queue.run(lambda: None, limit_seconds=5))
    await asyncio.sleep(0.01)
    waiting.cancel()
    await asyncio.sleep(0.01)
    assert queue.depth == 1
    release.set()
    await running
