"""The model servers in the rendered chart: run here by default, or named.

Kept apart from `test_chart.py` for the same reason Qdrant is: these are
about where one dependency lives and who may reach it.
"""

from __future__ import annotations

from typing import Any

from test_chart import containers, env_of, named, of_kind, refusal, render, workloads
from test_chart_qdrant import admitted

OWN_EMBEDDER = (
    "inference.embeddings.bundled.enabled=false",
    "inference.embeddings.baseUrl=http://vllm.models:8000/v1",
    "inference.embeddings.model=Qwen/Qwen3-Embedding-0.6B",
)
OWN_RERANKER = (
    "inference.rerank.bundled.enabled=false",
    "inference.rerank.baseUrl=http://vllm.models:8000",
    "inference.rerank.model=BAAI/bge-reranker-v2-m3",
)
SERVERS = ("embeddings", "reranker")


def args_of(statefulset: dict[str, Any]) -> list[str]:
    return containers(statefulset)[0]["args"]


def flag(args: list[str], name: str) -> str:
    return args[args.index(name) + 1]


def test_by_default_this_release_runs_both_model_servers_and_retrieval_uses_them() -> None:
    """So an install with no inference settings can index and search."""
    rendered = render()

    retrieval = env_of(named(rendered, "Deployment", "-retrieval"))
    assert retrieval["PRIMER_EMBEDDING_BASE_URL"] == "http://primer-primer-embeddings:8080/v1"
    assert retrieval["PRIMER_RERANK_BASE_URL"] == "http://primer-primer-reranker:8080"
    for server in SERVERS:
        service = named(rendered, "Service", f"-{server}")
        assert [port["port"] for port in service["spec"]["ports"]] == [8080]


def test_the_bundled_embedder_answers_to_the_model_retrieval_and_the_workers_are_told() -> None:
    """One value says which model: the server's name, the queries, and the chunk sizes.

    llama-server would otherwise answer to the file it loaded, and Retrieval
    describing one model while asking for another embeds at one width and
    stores at another, or sizes chunks with the wrong tokenizer - neither
    of which fails loudly.
    """
    rendered = render(
        "inference.embeddings.model=Qwen/Qwen3-Embedding-4B",
        "inference.embeddings.bundled.gguf=Qwen/Qwen3-Embedding-4B-GGUF:Q8_0",
    )

    args = args_of(named(rendered, "StatefulSet", "-embeddings"))
    assert flag(args, "-hf") == "Qwen/Qwen3-Embedding-4B-GGUF:Q8_0"
    assert flag(args, "--alias") == "Qwen/Qwen3-Embedding-4B"
    assert env_of(named(rendered, "Deployment", "-retrieval"))["PRIMER_EMBEDDING_MODEL"] == (
        "Qwen/Qwen3-Embedding-4B"
    )
    assert env_of(named(rendered, "Deployment", "-worker-parse"))["PRIMER_EMBEDDING_MODEL"] == (
        "Qwen/Qwen3-Embedding-4B"
    )


def test_the_default_embedder_pools_the_last_token() -> None:
    """Qwen3-Embedding is trained on the last token, and mean pooling retrieves badly."""
    args = args_of(named(render(), "StatefulSet", "-embeddings"))
    assert "--embedding" in args
    assert flag(args, "--pooling") == "last"


def test_the_bundled_reranker_serves_rerank() -> None:
    """Without --reranking, llama-server has no /rerank and every search falls back."""
    args = args_of(named(render(), "StatefulSet", "-reranker"))
    assert "--reranking" in args
    assert flag(args, "--alias") == "Qwen/Qwen3-Reranker-0.6B"


def test_each_bundled_slot_holds_the_declared_input_limit() -> None:
    """llama-server refuses an input that overflows its slot, rather than truncating.

    `--ctx-size` alone is divided between the slots, so four of them would
    each get a quarter, and a chunk the worker was told fits would come back
    as a 400. The batch has to hold a whole input too: an embedding is one
    pass.
    """
    rendered = render(
        "inference.embeddings.capabilities.maxInputTokens=1024",
        "inference.embeddings.bundled.parallel=3",
    )
    args = args_of(named(rendered, "StatefulSet", "-embeddings"))
    assert flag(args, "--kv-unified-per-slot") == "1024"
    assert "--ctx-size" not in args
    assert flag(args, "--parallel") == "3"
    assert flag(args, "--ubatch-size") == "1024"


def test_the_declared_embedder_limits_reach_retrieval_and_the_parse_worker() -> None:
    """Retrieval batches inside them; the worker refuses chunks that would not fit.

    Only the worker needs the input limit: it decides how long a chunk may
    be, and Retrieval embeds what it is given.
    """
    rendered = render(
        "inference.embeddings.capabilities.maxInputTokens=1024",
        "inference.embeddings.capabilities.batchSize=8",
    )
    retrieval = env_of(named(rendered, "Deployment", "-retrieval"))
    assert retrieval["PRIMER_EMBEDDING_BATCH_SIZE"] == "8"
    assert "PRIMER_EMBEDDING_MAX_INPUT_TOKENS" not in retrieval
    worker = env_of(named(rendered, "Deployment", "-worker-parse"))
    assert worker["PRIMER_EMBEDDING_MAX_INPUT_TOKENS"] == "1024"


def test_the_declared_limits_apply_to_your_own_embedder_too() -> None:
    """A vLLM started with --max-model-len refuses longer inputs the same way."""
    rendered = render(*OWN_EMBEDDER, "inference.embeddings.capabilities.maxInputTokens=512")
    worker = env_of(named(rendered, "Deployment", "-worker-parse"))
    assert worker["PRIMER_EMBEDDING_MAX_INPUT_TOKENS"] == "512"


def test_the_bundled_servers_run_hardened_with_a_cache_that_survives_restarts() -> None:
    """A read-only root works only if every path llama-server writes is mounted.

    The cache is a volume, not an emptyDir, because the model is gigabytes
    and would otherwise be downloaded again on every restart.
    """
    rendered = render()
    for server in SERVERS:
        statefulset = named(rendered, "StatefulSet", f"-{server}")
        pod = statefulset["spec"]["template"]["spec"]
        container = containers(statefulset)[0]

        assert pod["securityContext"]["runAsNonRoot"] is True, server
        assert pod["automountServiceAccountToken"] is False, server
        assert container["securityContext"]["readOnlyRootFilesystem"] is True, server
        assert container["securityContext"]["capabilities"]["drop"] == ["ALL"], server
        assert {mount["mountPath"] for mount in container["volumeMounts"]} == {"/data", "/tmp"}  # noqa: S108
        env = {entry["name"]: entry["value"] for entry in container["env"]}
        assert env["LLAMA_CACHE"] == "/data", server
        claims = statefulset["spec"]["volumeClaimTemplates"]
        assert [claim["metadata"]["name"] for claim in claims] == ["model-cache"], server


def test_only_retrieval_may_reach_the_model_servers() -> None:
    """They have no authentication of their own."""
    rendered = render()
    for server in SERVERS:
        policy = named(rendered, "NetworkPolicy", f"-allow-{server}")
        assert admitted(policy) == {"retrieval"}, server


def test_your_own_embedder_replaces_the_bundled_one() -> None:
    rendered = render(*OWN_EMBEDDER)

    assert not [doc for doc in rendered if doc["metadata"]["name"].endswith("-embeddings")]
    assert "primer-primer-embeddings" not in workloads(rendered)
    retrieval = env_of(named(rendered, "Deployment", "-retrieval"))
    assert retrieval["PRIMER_EMBEDDING_BASE_URL"] == "http://vllm.models:8000/v1"


def test_an_embedding_url_and_a_bundled_embedder_together_are_refused() -> None:
    """Either choice would leave the other unused, usually the one meant."""
    message = refusal("inference.embeddings.baseUrl=http://vllm.models:8000/v1")
    assert "inference.embeddings.baseUrl and inference.embeddings.bundled.enabled" in message


def test_no_embedder_at_all_is_refused() -> None:
    """Nothing could be indexed or searched; refusing says so before install."""
    message = refusal("inference.embeddings.bundled.enabled=false")
    assert "inference.embeddings.baseUrl is required" in message


def test_your_own_reranker_replaces_the_bundled_one() -> None:
    rendered = render(*OWN_RERANKER)

    assert not [doc for doc in rendered if doc["metadata"]["name"].endswith("-reranker")]
    retrieval = env_of(named(rendered, "Deployment", "-retrieval"))
    assert retrieval["PRIMER_RERANK_BASE_URL"] == "http://vllm.models:8000"
    assert retrieval["PRIMER_RERANK_MODEL"] == "BAAI/bge-reranker-v2-m3"


def test_turning_the_bundled_reranker_off_searches_without_one() -> None:
    """No rerank settings reach Retrieval, so it keeps the vector ordering."""
    rendered = render("inference.rerank.bundled.enabled=false")

    assert not [doc for doc in rendered if doc["metadata"]["name"].endswith("-reranker")]
    retrieval = env_of(named(rendered, "Deployment", "-retrieval"))
    assert not [name for name in retrieval if name.startswith("PRIMER_RERANK_")]


def test_a_rerank_url_and_a_bundled_reranker_together_are_refused() -> None:
    message = refusal("inference.rerank.baseUrl=http://vllm.models:8000")
    assert "inference.rerank.baseUrl and inference.rerank.bundled.enabled" in message


def test_no_model_server_is_rendered_when_both_are_your_own() -> None:
    rendered = render(*OWN_EMBEDDER, *OWN_RERANKER)

    names = {doc["metadata"]["name"] for doc in rendered}
    assert not [name for name in names if name.endswith(("-embeddings", "-reranker"))]
    assert of_kind(rendered, "StatefulSet")  # Qdrant still is, so this saw a render
