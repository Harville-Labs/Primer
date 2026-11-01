"""A transcoder repository and a stand-in graph builder, for the graph tests.

The builder here is not circuit-tracer: the real one is tested in its own
project, against a real model. This one records what Lab asked of it and
then does what the test told it to - answer, hang, crash, or write
nonsense - so the tests can hold Lab to what it does with each.
"""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import torch
from primer_contracts.lab_graphs import BuiltGraph, GraphEdge, GraphNode
from safetensors.torch import save_file

TRANSCODERS = "lab/tiny-transcoders"

CANNED = BuiltGraph(
    tokens=["<eos>", "hello"],
    nodes=[
        GraphNode(id="t@1", kind="token", position=1, token="hello", influence=0.5),
        GraphNode(id="f0.3@1", kind="feature", layer=0, position=1, feature=3, influence=0.2),
        GraphNode(id="l5", kind="logit", position=1, token="w5", probability=0.4, influence=0),
    ],
    edges=[
        GraphEdge(source="t@1", target="f0.3@1", weight=0.7),
        GraphEdge(source="f0.3@1", target="l5", weight=-0.3),
    ],
    dropped_edges=0,
)

MAIN = """
import json, os, sys, time
from pathlib import Path
here = Path(__file__).parent.parent
(here / "request.json").write_text(sys.stdin.read())
(here / "environment.json").write_text(json.dumps(dict(os.environ)))
mode = (here / "mode").read_text()
if mode == "graph":
    sys.stdout.write((here / "graph.json").read_text())
elif mode == "hang":
    time.sleep(60)
elif mode == "crash":
    sys.stderr.write("RuntimeError: the builder fell over\\n")
    sys.exit(1)
else:
    sys.stdout.write("not a graph")
"""


class FakeBuilder:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        package = directory / "primer_graphs"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("")
        (package / "__main__.py").write_text(MAIN)
        (directory / "graph.json").write_text(CANNED.model_dump_json())
        self.python = directory / "python"
        # An interpreter that finds this package first, as the builder's
        # own environment finds the real one.
        self.python.write_text(
            f'#!/bin/sh\nPYTHONPATH="{directory}" exec "{sys.executable}" "$@"\n'
        )
        self.python.chmod(self.python.stat().st_mode | stat.S_IXUSR)
        self.does("graph")

    def does(self, mode: str) -> None:
        (self.directory / "mode").write_text(mode)

    @property
    def asked(self) -> dict | None:
        path = self.directory / "request.json"
        return json.loads(path.read_text()) if path.exists() else None

    @property
    def environment(self) -> dict[str, str]:
        return json.loads((self.directory / "environment.json").read_text())


def build_transcoders(
    directory: Path,
    *,
    layers: int = 2,
    d_model: int = 16,
    width: int = 32,
    config: str | None = None,
    widths: dict[int, int] | None = None,
) -> Path:
    """A transcoder repository laid out as circuit-tracer publishes one."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "config.yaml").write_text(
        config
        or 'model_kind: "transcoder_set"\nmodel_name: "lab/tiny"\n'
        'feature_input_hook: "mlp.hook_in"\nfeature_output_hook: "mlp.hook_out"\n'
    )
    for layer in range(layers):
        wide = (widths or {}).get(layer, width)
        save_file(
            {
                "W_enc": torch.zeros(wide, d_model),
                "W_dec": torch.zeros(wide, d_model),
                "b_enc": torch.zeros(wide),
                "b_dec": torch.zeros(d_model),
                "activation_function.threshold": torch.zeros(wide),
            },
            str(directory / f"layer_{layer}.safetensors"),
        )
    return directory
