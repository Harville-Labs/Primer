"""Attribution graphs: which features carried a prompt to the token the model predicted.

A graph is built from a model and a set of transcoders - one per layer,
each trained to stand in for that layer's MLP with sparse features - by
circuit-tracer. Lab does not import circuit-tracer: it needs an older
`transformers` than Lab runs on, so a separate program builds the graph and
Lab talks to it with `GraphBuild` and `BuiltGraph`. See ADR 0009.

`GraphRequest` and `AttributionGraph` are what the web app sends and gets.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from primer_contracts.base import WireModel
from primer_contracts.lab import VERBATIM, LabModelStatus, TranscoderStatus

#: The hooks the published Qwen3 transcoders read and write. A transcoder
#: trained on any other pair stands in for something else, and its graph
#: would attribute through the wrong part of the model.
TRANSCODER_INPUT_HOOK = "mlp.hook_in"
TRANSCODER_OUTPUT_HOOK = "mlp.hook_out"

#: Node influence is a cumulative share of everything that reached the
#: logits, in [0, 1], and a node is kept when its share is at most the
#: threshold. Lower keeps fewer, more influential nodes.
DEFAULT_NODE_THRESHOLD = 0.8
DEFAULT_EDGE_THRESHOLD = 0.98


class GraphRequest(WireModel):
    model_config = VERBATIM

    #: Read as plain text, never through a chat template. See `graphs` in Lab.
    prompt: str = Field(min_length=1, max_length=4000)
    #: The token to explain. Unset explains the tokens the model found most
    #: likely, as many as make up most of the probability.
    target: str | None = Field(default=None, min_length=1, max_length=100)
    node_threshold: float = Field(default=DEFAULT_NODE_THRESHOLD, ge=0, le=1)
    edge_threshold: float = Field(default=DEFAULT_EDGE_THRESHOLD, ge=0, le=1)


class GraphBuild(WireModel):
    """What Lab asks the graph builder for. Every bound here is Lab's to set."""

    model_dir: str = Field(min_length=1)
    #: One file per layer, in layer order.
    transcoders: list[str] = Field(min_length=1)
    #: Names the transcoder set in the graph, as `repository@commit`.
    scan: str = Field(min_length=1)
    #: Tokenized by Lab, so the builder never decides what a prompt is.
    token_ids: list[int] = Field(min_length=1)
    target_id: int | None = Field(default=None, ge=0)
    max_feature_nodes: int = Field(ge=1)
    max_logits: int = Field(ge=1)
    max_edges: int = Field(ge=1)
    node_threshold: float = Field(ge=0, le=1)
    edge_threshold: float = Field(ge=0, le=1)
    device: Literal["cpu", "cuda"]
    dtype: Literal["float32", "bfloat16", "float16"]


class GraphNode(WireModel):
    id: str
    #: A transcoder feature; the part of a layer's MLP output the transcoder
    #: did not reconstruct; an input token; or a predicted token.
    kind: Literal["feature", "error", "token", "logit"]
    #: None for tokens and logits, which sit below and above every layer.
    layer: int | None = Field(default=None, ge=0)
    position: int = Field(ge=0)
    feature: int | None = Field(default=None, ge=0)
    activation: float | None = None
    #: Cumulative share of influence on the logits; see the thresholds.
    influence: float = Field(ge=0, le=1)
    #: The token's text, for token and logit nodes.
    token: str | None = None
    #: How likely the model found a logit node's token.
    probability: float | None = Field(default=None, ge=0, le=1)


class GraphEdge(WireModel):
    source: str
    target: str
    #: Direct effect of the source on the target. Negative inhibits.
    weight: float


class BuiltGraph(WireModel):
    """A pruned graph, as the builder returns it."""

    #: The prompt as the model read it, including the token circuit-tracer
    #: puts in front of it, which carries no attribution.
    tokens: list[str]
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    #: Edges the pruning kept that were left out to bound the response,
    #: the weakest first.
    dropped_edges: int = Field(ge=0)


class AttributionGraph(WireModel):
    model: LabModelStatus
    transcoders: TranscoderStatus
    graph: BuiltGraph
