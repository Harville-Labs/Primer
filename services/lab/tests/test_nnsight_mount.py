"""Lab keeps serving after nnsight has given every object a `save`."""

from __future__ import annotations

import subprocess
import sys


def test_sync_routes_still_reach_the_thread_pool_after_a_trace() -> None:
    """The first `.save()` in a trace mounts `save` on `object` for good.

    anyio imports its modules lazily, and a `TypedAttributeSet` subclass
    checks every public name it can see for an annotation when its class
    is defined. Defined after the mount, `SocketAttribute` sees `save` and
    refuses, so the first request that needs the thread pool - any route
    with a sync dependency, such as status - fails with a 500, and so does
    every one after it, because a failed import is not cached. Lab loads
    and traces its startup model before it serves anything, so this was
    the state of every deployment that lost the race.

    In a subprocess because the mount cannot be undone, and because the
    test client has already imported anyio's backend, which hides the bug.
    """
    script = """
import asyncio

import primer_lab.app  # noqa: F401 - the import is what is under test
from nnsight.intervention.tracing.globals import _ensure_mounted

_ensure_mounted()
assert hasattr(object(), "save"), "the mount did not happen, so nothing was tested"

import anyio.to_thread

print("ANSWER:" + asyncio.run(anyio.to_thread.run_sync(lambda: "served")))
"""
    finished = subprocess.run(  # noqa: S603 - a fixed interpreter on a generated script
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert finished.returncode == 0, finished.stderr[-2000:]
    assert "ANSWER:served" in finished.stdout
