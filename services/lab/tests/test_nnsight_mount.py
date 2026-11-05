"""A trace must leave Python's objects as it found them.

nnsight, by default, gives every object in the interpreter a `save`
method the first time a trace runs. anyio imports its asyncio backend
lazily, on the first call that hands work to a thread, and that import
defines classes whose base rejects any public attribute it cannot find an
annotation for. Once `save` is everywhere, the import raises, and every
route Starlette runs in a thread pool answers 500 until Lab restarts.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

#: Run in a fresh interpreter: the mount is permanent and process-wide, and
#: this test process has long since imported anyio's backend, which would
#: hide the failure.
SCRIPT = """
import sys
from pathlib import Path

import torch
from primer_lab.loader import LoadPlan, load_model

plan = LoadPlan(
    directory=Path(sys.argv[1]), architecture="qwen3", commit=None, device="cpu", dtype="float32"
)
model = load_model(plan).model
with torch.no_grad(), model.trace(torch.tensor([[1, 2, 3]])):
    model.layers_output[0][0].norm()

assert not hasattr(object(), "save"), "the trace gave every object a save method"

import anyio._backends._asyncio  # noqa: E402,F401  the import a route makes lazily
"""


def test_a_trace_does_not_break_the_thread_pool_routes_run_in(tiny_model: Path) -> None:
    """The first trace, then the first threaded route, as Lab meets them after starting."""
    result = subprocess.run(  # noqa: S603 - this interpreter, a fixed script
        [sys.executable, "-c", SCRIPT, str(tiny_model)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert result.returncode == 0, result.stderr
