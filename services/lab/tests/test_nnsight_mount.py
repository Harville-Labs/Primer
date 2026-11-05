"""Lab keeps serving after nnsight has given every object a `save`."""

from __future__ import annotations

import subprocess
import sys


def test_sync_routes_still_reach_the_thread_pool_after_a_trace() -> None:
    """The first `.save()` in a trace mounts `save` on `object` for good.

    Defined after the mount, an anyio `TypedAttributeSet` sees `save` and
    refuses, so every request needing the thread pool - any route with a
    sync dependency, such as status - would fail with a 500. The app
    module defines them all first.

    It is checked against anyio's source, rather than against the three
    names the app imports, so an anyio release that adds a fourth fails
    here and not in a deployment. In a subprocess because the mount cannot
    be undone, and because the test client has already imported anyio's
    backend, which hides the bug.
    """
    script = """
import asyncio
import pathlib
import re

import anyio
import primer_lab.app  # noqa: F401 - the import is what is under test
from anyio import TypedAttributeSet
from nnsight.intervention.tracing.globals import _ensure_mounted

source = pathlib.Path(anyio.__file__).parent
declared = {
    name
    for path in source.rglob("*.py")
    for name in re.findall(r"^class (\\w+)\\(TypedAttributeSet\\)", path.read_text(), re.M)
}
assert declared, "found no attribute sets in anyio, so nothing was checked"
missing = declared - {cls.__name__ for cls in TypedAttributeSet.__subclasses__()}
assert not missing, f"import these in primer_lab.app: {sorted(missing)}"

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
