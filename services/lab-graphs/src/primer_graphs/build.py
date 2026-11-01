"""Build one attribution graph with circuit-tracer and describe it for the wire.

Everything that decides what is built - the prompt's tokens, the files, the
bounds - comes from Lab in a `GraphBuild`. This module loads what it was
pointed at, attributes, prunes, and says what is left.
"""

from __future__ import annotations

from collections.abc import Callable

import torch
from circuit_tracer.attribution.attribute import attribute
from circuit_tracer.graph import Graph, prune_graph
from circuit_tracer.replacement_model.replacement_model_nnsight import NNSightReplacementModel
from circuit_tracer.transcoder.single_layer_transcoder import load_transcoder_set
from primer_contracts.lab_graphs import (
    TRANSCODER_INPUT_HOOK,
    TRANSCODER_OUTPUT_HOOK,
    BuiltGraph,
    GraphBuild,
    GraphEdge,
    GraphNode,
)

#: Source nodes per backward pass. circuit-tracer's default of 512 is sized
#: for a large GPU; this keeps a CPU build's memory near the model's own.
BATCH_SIZE = 128


def build(request: GraphBuild) -> BuiltGraph:
    device = torch.device(request.device)
    dtype = getattr(torch, request.dtype)
    transcoders = load_transcoder_set(
        dict(enumerate(request.transcoders)),
        scan=request.scan,
        feature_input_hook=TRANSCODER_INPUT_HOOK,
        feature_output_hook=TRANSCODER_OUTPUT_HOOK,
        device=device,
        dtype=dtype,
        # Read each layer's weights when attribution reaches it rather than
        # all at once, which for Qwen3-4B is 60 GB.
        lazy_encoder=True,
        lazy_decoder=True,
    )
    model = NNSightReplacementModel.from_pretrained_and_transcoders(
        request.model_dir, transcoders, device=device, dtype=dtype
    )
    targets = None if request.target_id is None else torch.tensor([request.target_id])
    graph = attribute(
        request.token_ids,
        model,
        attribution_targets=targets,
        max_n_logits=request.max_logits,
        max_feature_nodes=request.max_feature_nodes,
        batch_size=BATCH_SIZE,
    )
    graph.to("cpu")
    return describe(
        graph,
        decode=lambda token: model.tokenizer.decode([token]),
        node_threshold=request.node_threshold,
        edge_threshold=request.edge_threshold,
        max_edges=request.max_edges,
    )


def describe(
    graph: Graph,
    *,
    decode: Callable[[int], str],
    node_threshold: float,
    edge_threshold: float,
    max_edges: int,
) -> BuiltGraph:
    """Prune `graph` and keep the nodes an edge reaches, plus every token and logit.

    The adjacency matrix's rows are targets and its columns sources. Its
    nodes are features, then one error per layer and position (layer-major),
    then input tokens, then logits.
    """
    node_mask, edge_mask, influence = prune_graph(graph, node_threshold, edge_threshold)
    n_features = len(graph.selected_features)
    n_layers = graph.cfg.n_layers
    n_pos = graph.n_pos
    errors_end = n_features + n_layers * n_pos
    tokens_end = errors_end + len(graph.input_tokens)
    input_tokens = [int(t) for t in graph.input_tokens.tolist()]

    def node(index: int) -> GraphNode:
        share = min(max(float(influence[index]), 0.0), 1.0)
        if index < n_features:
            selected = graph.selected_features[index]
            layer, position, feature = (int(v) for v in graph.active_features[selected].tolist())
            return GraphNode(
                id=f"f{layer}.{feature}@{position}",
                kind="feature",
                layer=layer,
                position=position,
                feature=feature,
                activation=float(graph.activation_values[selected]),
                influence=share,
            )
        if index < errors_end:
            layer, position = divmod(index - n_features, n_pos)
            return GraphNode(
                id=f"e{layer}@{position}",
                kind="error",
                layer=layer,
                position=position,
                influence=share,
            )
        if index < tokens_end:
            position = index - errors_end
            return GraphNode(
                id=f"t@{position}",
                kind="token",
                position=position,
                token=decode(input_tokens[position]),
                influence=share,
            )
        target = index - tokens_end
        vocab = graph.logit_targets[target].vocab_idx
        return GraphNode(
            id=f"l{vocab}",
            kind="logit",
            position=n_pos - 1,
            token=decode(vocab),
            probability=min(max(float(graph.logit_probabilities[target]), 0.0), 1.0),
            influence=share,
        )

    kept: dict[int, GraphNode] = {}

    def keep(index: int) -> GraphNode:
        if index not in kept:
            kept[index] = node(index)
        return kept[index]

    weights = graph.adjacency_matrix
    targets, sources = (edge_mask & node_mask[:, None] & node_mask[None, :]).nonzero(as_tuple=True)
    found: list[tuple[float, int, int]] = [
        (float(weights[t, s]), int(s), int(t))
        for t, s in zip(targets.tolist(), sources.tolist(), strict=True)
    ]
    found.sort(key=lambda edge: abs(edge[0]), reverse=True)
    edges = [
        GraphEdge(source=keep(s).id, target=keep(t).id, weight=w) for w, s, t in found[:max_edges]
    ]
    for index in range(errors_end, len(influence)):
        keep(index)
    return BuiltGraph(
        tokens=[decode(t) for t in input_tokens],
        nodes=[kept[i] for i in sorted(kept)],
        edges=edges,
        dropped_edges=max(len(found) - max_edges, 0),
    )
