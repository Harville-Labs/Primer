"""Static policy checks on the Compose deployment.

These read the resolved configuration rather than starting anything. What
they guard is the set of mistakes that are invisible until something goes
wrong in production: a container that can write its own filesystem, a
credential with a default, a service reachable that should not be.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

COMPOSE_DIR = Path(__file__).resolve().parents[2] / "deploy" / "compose"
BASE = COMPOSE_DIR / "compose.yaml"

#: Primer's own containers. The dependencies are third-party images whose
#: hardening is not ours to assert.
APPLICATION_SERVICES = ("control", "chat", "retrieval", "worker-parse", "worker-index", "web")

WORKERS = ("worker-parse", "worker-index")

#: Every queue Control publishes to. A queue no worker consumes is a stage
#: whose documents sit `queued` forever without an error anywhere.
QUEUES = {"ingestion.parse", "ingestion.embed", "ingestion.index", "ingestion.delete"}

#: Placeholders that satisfy `${VAR:?}` so the file can be resolved. None is
#: a real credential, and none has a default in the file itself.
RESOLVE_ENV = {
    "POSTGRES_PASSWORD": "resolve-only",
    "RABBITMQ_PASSWORD": "resolve-only",
    "PRIMER_INTERNAL_API_TOKEN": "resolve-only",
}

#: The bundled model servers. Not hardened by `APPLICATION_SERVICES`'s test
#: alone, because they are a third-party image run under Primer's rules.
MODEL_SERVERS = ("embeddings", "reranker")
DEFAULT_EMBEDDING_MODEL = "onnx-community/Qwen3-Embedding-0.6B-ONNX"


def resolve(*files: Path, env: dict[str, str] | None = None) -> dict[str, Any]:
    """Ask Docker to resolve the files, so the test sees what Compose sees.

    `--env-file` names `env.example` explicitly, because Compose otherwise
    loads whatever `deploy/compose/.env` a developer happens to have. That
    file is gitignored and full of real credentials on any machine the
    stack has actually been run on, so the secrets check below was reading
    private local state and asserting things about it - passing in CI only
    because CI has no `.env` to find.

    The committed example is what these tests mean to check anyway: it is
    the file an operator is told to copy.
    """
    command = ["docker", "compose", "--env-file", str(COMPOSE_DIR / "env.example")]
    for path in files:
        command += ["-f", str(path)]
    command.append("config")
    result = subprocess.run(  # noqa: S603 - a fixed command on repository files
        command,
        capture_output=True,
        text=True,
        env={**os.environ, **RESOLVE_ENV, **(env or {})},
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(f"docker compose config failed:\n{result.stderr}")
    return yaml.safe_load(result.stdout)


@pytest.fixture(scope="module")
def compose_config() -> dict[str, Any]:
    if shutil.which("docker") is None:
        pytest.skip("docker is not installed")
    return resolve(BASE)


@pytest.mark.parametrize("name", APPLICATION_SERVICES)
def test_application_services_are_hardened(compose_config: dict[str, Any], name: str) -> None:
    """The plan's case: read-only roots and no privilege escalation."""
    service = compose_config["services"][name]

    assert service["read_only"] is True
    assert service["security_opt"] == ["no-new-privileges:true"]
    assert service["cap_drop"] == ["ALL"]


def test_nothing_mounts_the_docker_socket(compose_config: dict[str, Any]) -> None:
    """A container with the socket is a container that is root on the host."""
    for service in compose_config["services"].values():
        for volume in service.get("volumes", []):
            source = volume.get("source", "") if isinstance(volume, dict) else str(volume)
            assert "docker.sock" not in source


def test_nothing_bind_mounts_a_host_path(compose_config: dict[str, Any]) -> None:
    """Named volumes only, so the stack is portable and cannot reach the host."""
    for name, service in compose_config["services"].items():
        for volume in service.get("volumes", []):
            kind = volume.get("type") if isinstance(volume, dict) else None
            assert kind != "bind", f"{name} bind-mounts a host path"


def test_credentials_have_no_defaults(compose_config: dict[str, Any]) -> None:
    """A password with a default is a password every deployment shares."""
    text = BASE.read_text()
    for variable in ("POSTGRES_PASSWORD", "RABBITMQ_PASSWORD", "PRIMER_INTERNAL_API_TOKEN"):
        assert f"${{{variable}:?" in text, f"{variable} must be required, not defaulted"
        # The bundled PostgreSQL's own copy may be empty, never a value: it
        # is interpolated even when the server is off, and refuses to start
        # without one when it is on.
        rest = text.replace("POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-}", "")
        assert f"${{{variable}:-" not in rest, f"{variable} must not have a default"


def test_the_resolved_configuration_contains_no_real_secrets(
    compose_config: dict[str, Any],
) -> None:
    """Only the placeholders this test supplied, and the committed example.

    Worth stating what this can and cannot catch: it reads `env.example`
    and the compose file, both of which are in the repository, so it
    guards against a credential being committed. A developer's own `.env`
    is deliberately out of scope - it is gitignored, it is meant to hold
    real keys, and resolving it here would fail this test on every machine
    where the stack works.
    """
    rendered = yaml.safe_dump(compose_config)
    assert "sk-" not in rendered
    assert "BEGIN PRIVATE KEY" not in rendered


def test_migrations_run_as_a_one_shot_before_the_services(
    compose_config: dict[str, Any],
) -> None:
    """Replicas racing to alter one schema is what this ordering prevents."""
    for migration in ("migrate-control", "migrate-chat"):
        assert compose_config["services"][migration]["restart"] == "no"

    assert (
        compose_config["services"]["control"]["depends_on"]["migrate-control"]["condition"]
        == "service_completed_successfully"
    )
    assert (
        compose_config["services"]["chat"]["depends_on"]["migrate-chat"]["condition"]
        == "service_completed_successfully"
    )


def test_the_worker_has_its_own_image(compose_config: dict[str, Any]) -> None:
    """Torch publishes no musl wheels, so the API services cannot share Alpine
    with the worker. Keeping them apart is what keeps the API image small."""
    api = compose_config["services"]["control"]["build"]["dockerfile"]
    assert api.endswith("Dockerfile.python")
    for name in WORKERS:
        assert compose_config["services"][name]["build"]["dockerfile"].endswith("Dockerfile.worker")

    for name in ("control", "chat", "retrieval"):
        assert compose_config["services"][name]["build"]["dockerfile"] == api


def worker_option(command: list[str], flag: str) -> str:
    assert flag in command, command
    return command[command.index(flag) + 1]


def test_every_queue_is_consumed_by_exactly_one_worker(compose_config: dict[str, Any]) -> None:
    consumed = [
        queue
        for name in WORKERS
        for queue in worker_option(compose_config["services"][name]["command"], "-Q").split(",")
    ]

    assert sorted(consumed) == sorted(QUEUES)


def test_parsing_does_not_share_a_process_with_the_stages_that_wait(
    compose_config: dict[str, Any],
) -> None:
    """One process for every queue sized its pool for parsing, which holds
    the CPU, when most of what it ran was a wait on a model endpoint - and
    a burst of uploads queued every embedding behind the parses."""
    parse = compose_config["services"]["worker-parse"]["command"]
    index = compose_config["services"]["worker-index"]["command"]

    assert worker_option(parse, "--pool") == "prefork"
    assert worker_option(parse, "--concurrency") == "1"
    assert worker_option(index, "--pool") == "threads"


def test_only_the_parse_worker_holds_the_model_cache(compose_config: dict[str, Any]) -> None:
    def volumes(name: str) -> set[str]:
        return {v["source"] for v in compose_config["services"][name]["volumes"]}

    assert "model-cache" in volumes("worker-parse")
    assert "model-cache" not in volumes("worker-index")


def test_by_default_the_stack_runs_its_own_model_servers(
    compose_config: dict[str, Any],
) -> None:
    """So a stack started with only credentials can index and search."""
    services = compose_config["services"]
    retrieval = services["retrieval"]

    assert retrieval["environment"]["PRIMER_EMBEDDING_BASE_URL"] == "http://embeddings:8080/v1"
    assert retrieval["environment"]["PRIMER_RERANK_BASE_URL"] == "http://reranker:8080"
    for server in MODEL_SERVERS:
        assert retrieval["depends_on"][server]["condition"] == "service_healthy"
        assert "ports" not in services[server], f"{server} has no authentication"


@pytest.mark.parametrize("name", MODEL_SERVERS)
def test_the_bundled_model_servers_run_hardened(compose_config: dict[str, Any], name: str) -> None:
    """A read-only root works only because the cache is the one place TEI writes."""
    service = compose_config["services"][name]

    assert service["read_only"] is True
    assert service["cap_drop"] == ["ALL"]
    assert service["user"] == "10001:10001"
    assert service["environment"]["HF_HOME"] == "/data"
    assert [volume["target"] for volume in service["volumes"]] == ["/data"]


def test_the_bundled_servers_accept_what_primer_sends_in_one_call(
    compose_config: dict[str, Any],
) -> None:
    """Below 32 inputs per call, every ingestion batch and shortlist is refused."""
    for name in MODEL_SERVERS:
        command = compose_config["services"][name]["command"]
        assert int(worker_option(command, "--max-client-batch-size")) >= 32, name
        assert int(worker_option(command, "--max-concurrent-requests")) >= 32, name


def test_the_chat_endpoint_is_not_required(compose_config: dict[str, Any]) -> None:
    """A provider can be added on the settings page instead."""
    assert "${PRIMER_CHAT_BASE_URL:?" not in BASE.read_text()
    assert compose_config["services"]["chat"]["environment"]["PRIMER_CHAT_BASE_URL"] == ""


def test_your_own_embedder_replaces_the_bundled_one() -> None:
    if shutil.which("docker") is None:
        pytest.skip("docker is not installed")
    theirs = "http://host.docker.internal:11434/v1"
    config = resolve(
        BASE,
        env={"PRIMER_EMBEDDING_BASE_URL": theirs, "PRIMER_EMBEDDING_MODEL": "qwen3-embedding:0.6b"},
    )

    assert "embeddings" not in config["services"]
    retrieval = config["services"]["retrieval"]
    assert retrieval["environment"]["PRIMER_EMBEDDING_BASE_URL"] == theirs
    assert retrieval["depends_on"]["embeddings"]["required"] is False


def test_an_empty_rerank_url_searches_without_a_reranker() -> None:
    """Set but empty stops the server and leaves Retrieval nothing to call."""
    if shutil.which("docker") is None:
        pytest.skip("docker is not installed")
    config = resolve(BASE, env={"PRIMER_RERANK_BASE_URL": ""})

    assert "reranker" not in config["services"]
    assert "embeddings" in config["services"]
    assert config["services"]["retrieval"]["environment"]["PRIMER_RERANK_BASE_URL"] == ""


def test_your_own_reranker_replaces_the_bundled_one() -> None:
    if shutil.which("docker") is None:
        pytest.skip("docker is not installed")
    config = resolve(BASE, env={"PRIMER_RERANK_BASE_URL": "http://tei.example:8080"})

    assert "reranker" not in config["services"]
    environment = config["services"]["retrieval"]["environment"]
    assert environment["PRIMER_RERANK_BASE_URL"] == "http://tei.example:8080"


def test_the_embedding_width_follows_from_the_model(compose_config: dict[str, Any]) -> None:
    """Required, it was a second place to state what the model name says.

    Retrieval takes the width from its table of known models and refuses to
    start on an unknown one with none, so Compose need not insist.
    """
    assert "${PRIMER_EMBEDDING_DIMENSIONS:?" not in BASE.read_text()


def test_both_sides_of_the_index_are_told_the_embedding_model(
    compose_config: dict[str, Any],
) -> None:
    """The worker cuts chunks with the tokenizer of the model Retrieval embeds with.

    Told different models, or only one of them, the chunks it produces are
    sized for something other than what reads them.
    """
    services = compose_config["services"]
    assert services["worker-parse"]["environment"]["PRIMER_EMBEDDING_MODEL"] == (
        DEFAULT_EMBEDDING_MODEL
    )
    assert services["retrieval"]["environment"]["PRIMER_EMBEDDING_MODEL"] == (
        DEFAULT_EMBEDDING_MODEL
    )
    assert worker_option(services["embeddings"]["command"], "--model-id") == (
        DEFAULT_EMBEDDING_MODEL
    )
    assert worker_option(services["embeddings"]["command"], "--pooling") == "last-token"


def test_the_chat_model_is_not_required(compose_config: dict[str, Any]) -> None:
    """Primer asks each provider what it serves, so naming one is optional.

    It was required, and that made a deployment fail to start over a value
    it did not need - while a name nothing served was offered in the picker
    as though it did. Naming a model now only says which to offer first.
    """
    assert "${PRIMER_CHAT_MODEL:?" not in BASE.read_text()


def test_the_profile_is_single_user_and_says_so(compose_config: dict[str, Any]) -> None:
    """Compose is one person on one machine, deliberately and permanently.

    Authentication belongs to the Kubernetes deployment, which has an
    ingress to put a proxy in front of. A proxy bolted onto Compose would
    look multi-user while one forgotten published port made it decorative.
    """
    for name in ("control", "chat"):
        assert compose_config["services"][name]["environment"]["PRIMER_AUTH_MODE"] == "disabled"


def test_auth_mode_is_not_configurable_here(compose_config: dict[str, Any]) -> None:
    """No variable to half-enable: `oidc` without an edge authenticates nobody."""
    text = BASE.read_text()
    assert "${PRIMER_AUTH_MODE" not in text


def test_nothing_is_published_beyond_loopback(compose_config: dict[str, Any]) -> None:
    """With no authentication, reachable means reachable *as the only user*."""
    published = [
        (name, port)
        for name, service in compose_config["services"].items()
        for port in service.get("ports", [])
    ]
    # Guard against a vacuous pass: if nothing is published at all, this test
    # would silently stop checking anything.
    assert published, "expected the stack to publish at least the web and control ports"

    for name, port in published:
        host_ip = port.get("host_ip") if isinstance(port, dict) else None
        assert host_ip == "127.0.0.1", f"{name} publishes on {host_ip or 'all interfaces'}"


def test_every_documented_variable_appears_in_the_example(
    compose_config: dict[str, Any],
) -> None:
    """An operator copying env.example should not hit a missing-variable error."""
    example = (COMPOSE_DIR / "env.example").read_text()
    for variable in RESOLVE_ENV:
        assert variable in example, f"{variable} is required but absent from env.example"


#: What turns the graph on: the profile, and a URL and password for it.
GRAPH_ENV = {
    "COMPOSE_PROFILES": "graph",
    "PRIMER_GRAPH_URL": "bolt://neo4j:7687",
    "PRIMER_GRAPH_PASSWORD": "resolve-only",
}


def resolve_with_graph() -> dict[str, Any]:
    if shutil.which("docker") is None:
        pytest.skip("docker is not installed")
    return resolve(BASE, env=GRAPH_ENV)


def test_the_graph_is_off_unless_its_profile_is_on(compose_config: dict[str, Any]) -> None:
    """A stack started as it always was must not start a Neo4j it never asked for."""
    assert "neo4j" not in compose_config["services"]
    assert "migrate-graph" not in compose_config["services"]


def test_the_graph_schema_is_applied_before_retrieval_starts() -> None:
    """Retrieval writing into a graph with no constraints duplicates every entity."""
    services = resolve_with_graph()["services"]

    assert services["migrate-graph"]["restart"] == "no"
    assert services["migrate-graph"]["depends_on"]["neo4j"]["condition"] == "service_healthy"
    waits = services["retrieval"]["depends_on"]["migrate-graph"]
    assert waits["condition"] == "service_completed_successfully"
    assert services["retrieval"]["environment"]["PRIMER_GRAPH_URL"] == "bolt://neo4j:7687"


def test_the_graph_password_has_no_default() -> None:
    """Empty when unset, never a value, so Neo4j refuses to start without one.

    It cannot be `:?` like the others: Compose interpolates every service
    whichever profiles are on, so that would refuse every stack without a graph.
    """
    text = BASE.read_text()
    assert "${PRIMER_GRAPH_PASSWORD:-}" in text
    assert "${PRIMER_GRAPH_PASSWORD:-" not in text.replace("${PRIMER_GRAPH_PASSWORD:-}", "")
    assert "neo4j/${PRIMER_GRAPH_PASSWORD:-}" in text


def test_neo4j_is_not_published() -> None:
    """Only Retrieval and the schema step talk to it, both on the Compose network."""
    assert "ports" not in resolve_with_graph()["services"]["neo4j"]


#: An operator's own PostgreSQL, in place of the bundled one.
EXTERNAL_DATABASE_URL = "postgresql://primer:theirs@db.example:5432/primer"


def database_users(config: dict[str, Any]) -> dict[str, str]:
    return {
        name: service["environment"]["PRIMER_DATABASE_URL"]
        for name, service in config["services"].items()
        if "PRIMER_DATABASE_URL" in (service.get("environment") or {})
    }


def test_every_database_user_shares_the_bundled_url(compose_config: dict[str, Any]) -> None:
    """One plain URL, from which each service chooses its own driver."""
    users = database_users(compose_config)

    assert set(users) >= {"control", "chat", *WORKERS, "migrate-control", "migrate-chat"}
    assert set(users.values()) == {"postgresql://primer:resolve-only@postgres:5432/primer"}
    assert "postgres" in compose_config["services"]


def test_your_own_postgresql_replaces_the_bundled_one() -> None:
    """No server started, nothing waiting on it, and no bundled password asked for."""
    if shutil.which("docker") is None:
        pytest.skip("docker is not installed")
    config = resolve(BASE, env={"PRIMER_DATABASE_URL": EXTERNAL_DATABASE_URL})
    users = database_users(config)

    assert "postgres" not in config["services"]
    assert set(users.values()) == {EXTERNAL_DATABASE_URL}
    for migration in ("migrate-control", "migrate-chat"):
        assert config["services"][migration]["depends_on"]["postgres"]["required"] is False


def test_retrieval_is_not_given_the_database(compose_config: dict[str, Any]) -> None:
    """Its vectors are in Qdrant and its graph in Neo4j.

    A password held by a service that never opens the database is one more
    place for it to leak from, and nothing would notice it being there.
    """
    assert "retrieval" not in database_users(compose_config)


def test_retrieval_searches_the_bundled_qdrant(compose_config: dict[str, Any]) -> None:
    services = compose_config["services"]

    assert services["retrieval"]["environment"]["PRIMER_QDRANT_URL"] == "http://qdrant:6333"
    assert services["retrieval"]["depends_on"]["qdrant"]["condition"] == "service_healthy"
    assert "ports" not in services["qdrant"]


def test_the_bundled_qdrant_reports_nothing_upstream(compose_config: dict[str, Any]) -> None:
    """Qdrant sends usage telemetry unless told not to, and Primer runs no trackers."""
    assert (
        compose_config["services"]["qdrant"]["environment"]["QDRANT__TELEMETRY_DISABLED"] == "true"
    )


def test_your_own_qdrant_replaces_the_bundled_one() -> None:
    if shutil.which("docker") is None:
        pytest.skip("docker is not installed")
    theirs = "https://qdrant.example:6333"
    config = resolve(BASE, env={"PRIMER_QDRANT_URL": theirs})

    assert "qdrant" not in config["services"]
    retrieval = config["services"]["retrieval"]
    assert retrieval["environment"]["PRIMER_QDRANT_URL"] == theirs
    assert retrieval["depends_on"]["qdrant"]["required"] is False
