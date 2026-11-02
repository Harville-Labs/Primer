"""Attribution graphs: which transcoder features carried a prompt to a token.

The graph is built by a separate program in its own environment (see ADR
0009 and `services/lab-graphs`), because circuit-tracer needs an older
`transformers` than Lab runs on. Lab decides everything about the build -
the tokens, the files, every bound - and the builder only computes. A build
takes the run queue like any other run, so nobody else's forward pass
shares the device with it.

The prompt is read as plain text and never through a chat template, and a
prompt holding a special token is refused. circuit-tracer puts one token in
front of the prompt and zeroes what reaches the graph from that position
alone; Qwen3 has no beginning-of-sequence token, so that token is
`<|endoftext|>`, which is the attention sink a Qwen3 model leans on. A
second special token later in the prompt would be a second sink nothing
zeroes, and the graph would credit it with most of everything. This is the
problem Neuronpedia met building graphs for Qwen3-4B.
"""

from __future__ import annotations

import logging
import os
import subprocess
from collections.abc import Mapping
from typing import Literal

from fastapi import APIRouter, Request, status
from primer_contracts.errors import ErrorCode
from primer_contracts.lab_graphs import AttributionGraph, BuiltGraph, GraphBuild, GraphRequest
from primer_service.errors import ProblemError
from pydantic import ValidationError

from primer_lab.config import Settings
from primer_lab.host import Resident
from primer_lab.identity import CurrentPrincipal
from primer_lab.inspection import special_ids
from primer_lab.queue import RunTimedOut
from primer_lab.routes import current_resident, refused, run_queued
from primer_lab.transcoders import TranscoderSet

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/lab", tags=["lab"])

#: Predicted tokens explained when no target is named: as many as make up
#: most of the probability, up to this many.
MAX_LOGITS = 10

#: Time for the builder to be killed and the queue to hear of it, past the
#: builder's own limit, so that limit is the one a user is told about.
QUEUE_GRACE_SECONDS = 30.0

#: The end of the builder's stderr kept in the log. Its own messages are
#: one line; a traceback from deep in torch is not.
STDERR_TAIL = 2000


class BuildFailed(RuntimeError):
    """The builder exited without a graph."""


@router.post(
    "/graph",
    summary="Build an attribution graph for a prompt",
    description=(
        "Traces which transcoder features carried the prompt to the token it names, or to "
        "the tokens the model found likeliest, and returns the graph pruned to the nodes "
        "and edges with the most influence. The prompt is read as plain text, without a "
        "chat template. A build holds the model while it runs, and takes seconds on a GPU "
        "and far longer on a CPU."
    ),
)
async def graph(
    request: Request, body: GraphRequest, principal: CurrentPrincipal
) -> AttributionGraph:
    settings: Settings = request.app.state.settings
    python = settings.lab_graph_python
    if python is None:
        raise refused(
            "This Lab builds no graphs",
            "Its operator has not installed the graph builder (PRIMER_LAB_GRAPH_PYTHON).",
        )
    resident = current_resident(request)
    transcoders = ready_transcoders(resident)
    build = plan_build(settings, resident, transcoders, body)
    limit = settings.lab_graph_timeout_seconds

    def work() -> BuiltGraph:
        return run_builder(str(python), build, timeout=limit)

    try:
        built = await run_queued(
            request,
            resident,
            work,
            limit_seconds=limit + QUEUE_GRACE_SECONDS,
            limit_setting="PRIMER_LAB_GRAPH_TIMEOUT_SECONDS",
        )
    except BuildFailed:
        raise ProblemError(
            code=ErrorCode.INTERNAL_ERROR,
            title="The graph could not be built",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The graph builder stopped without a graph. Lab's log says why; trying "
            "again with the same prompt will most likely fail the same way.",
        ) from None
    return AttributionGraph(model=resident.status(), transcoders=transcoders.status(), graph=built)


def ready_transcoders(resident: Resident) -> TranscoderSet:
    attachment = resident.transcoders
    if attachment is None:
        raise refused(
            "No transcoders for this model",
            "This deployment has no transcoders for the model Lab has loaded "
            "(PRIMER_LAB_TRANSCODERS), so it cannot build a graph for it.",
        )
    if attachment.ready is None:
        state = attachment.status
        raise ProblemError(
            code=ErrorCode.CONFLICT,
            title="The transcoders are not ready",
            status_code=status.HTTP_409_CONFLICT,
            detail=state.error
            if state.state == "failed"
            else "Lab is still fetching them. Try again once its status says they are ready.",
        )
    return attachment.ready


def plan_build(
    settings: Settings, resident: Resident, transcoders: TranscoderSet, body: GraphRequest
) -> GraphBuild:
    """Everything the builder is told, decided before the build queues."""
    tokenizer = resident.loaded.model.tokenizer
    ids = tokenizer.encode(body.prompt, add_special_tokens=False)
    special = special_ids(tokenizer)
    marked = [tokenizer.decode([i]) for i in ids if i in special]
    if marked:
        raise refused(
            "The prompt holds a special token",
            f"It contains {marked[0]}. A graph credits a special token with much of what "
            "the model does, so it is read from plain text only.",
        )
    if len(ids) > settings.lab_graph_max_prompt_tokens:
        raise refused(
            "The prompt is too long for a graph",
            f"It is {len(ids)} tokens, and this Lab builds graphs for at most "
            f"{settings.lab_graph_max_prompt_tokens} (PRIMER_LAB_GRAPH_MAX_PROMPT_TOKENS).",
        )
    target = None
    if body.target is not None:
        encoded = tokenizer.encode(body.target, add_special_tokens=False)
        if len(encoded) != 1:
            raise refused(
                "The target is not one token",
                f"{body.target!r} is {len(encoded)} tokens for this model. A graph explains "
                "one; a word after a space is usually written with the space in front.",
            )
        target = encoded[0]
    plan = resident.loaded.plan
    return GraphBuild(
        model_dir=str(plan.directory),
        transcoders=[str(path) for path in transcoders.files],
        scan=transcoders.scan,
        token_ids=ids,
        target_id=target,
        max_feature_nodes=settings.lab_graph_max_feature_nodes,
        max_logits=MAX_LOGITS,
        max_edges=settings.lab_graph_max_edges,
        node_threshold=body.node_threshold,
        edge_threshold=body.edge_threshold,
        device=plan.device,
        dtype=builder_dtype(plan.dtype),
    )


def builder_dtype(dtype: str) -> Literal["float32", "bfloat16", "float16"]:
    if dtype not in ("float32", "bfloat16", "float16"):
        raise ValueError(f"the loader planned {dtype}, which the builder cannot load")
    return dtype


def builder_environment(environ: Mapping[str, str]) -> dict[str, str]:
    """Lab's environment without Lab's own settings.

    The builder is told everything it needs on stdin, and Lab's settings
    include its database URL. torch's and CUDA's own variables pass through.
    """
    return {name: value for name, value in environ.items() if not name.startswith("PRIMER_")}


def run_builder(python: str, build: GraphBuild, *, timeout: float) -> BuiltGraph:
    """Run the builder once and read its graph, on the queue's thread.

    The builder is killed at `timeout`: unlike a forward pass in this
    process, a separate one can be stopped, so a graph that runs over frees
    the queue rather than holding it until it finishes anyway.
    """
    try:
        result = subprocess.run(  # noqa: S603 - the operator's interpreter, no shell, fixed module
            [python, "-m", "primer_graphs"],
            input=build.model_dump_json(),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=builder_environment(os.environ),
        )
    except subprocess.TimeoutExpired:
        raise RunTimedOut(timeout) from None
    if result.returncode != 0:
        logger.warning(
            "the graph builder exited %d: %s", result.returncode, result.stderr[-STDERR_TAIL:]
        )
        raise BuildFailed
    try:
        return BuiltGraph.model_validate_json(result.stdout)
    except ValidationError as error:
        logger.warning("the graph builder wrote something other than a graph: %s", error)
        raise BuildFailed from None
