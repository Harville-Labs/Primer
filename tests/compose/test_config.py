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
APPLICATION_SERVICES = ("control", "chat", "retrieval", "worker", "web")

#: Placeholders that satisfy `${VAR:?}` so the file can be resolved. None is
#: a real credential, and none has a default in the file itself.
RESOLVE_ENV = {
    "POSTGRES_PASSWORD": "resolve-only",
    "RABBITMQ_PASSWORD": "resolve-only",
    "PRIMER_INTERNAL_API_TOKEN": "resolve-only",
    "PRIMER_CHAT_BASE_URL": "http://model/v1",
    "PRIMER_CHAT_MODEL": "test-model",
    "PRIMER_EMBEDDING_BASE_URL": "http://model/v1",
    "PRIMER_EMBEDDING_MODEL": "test-embed",
    "PRIMER_EMBEDDING_DIMENSIONS": "768",
}


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
    worker = compose_config["services"]["worker"]["build"]["dockerfile"]

    assert api.endswith("Dockerfile.python")
    assert worker.endswith("Dockerfile.worker")
    assert api != worker

    for name in ("control", "chat", "retrieval"):
        assert compose_config["services"][name]["build"]["dockerfile"] == api


def test_no_model_server_is_bundled(compose_config: dict[str, Any]) -> None:
    """Primer ships no model; the endpoint is the operator's to provide."""
    images = [service.get("image", "") for service in compose_config["services"].values()]
    for marker in ("ollama", "vllm", "llama"):
        assert not any(marker in image.lower() for image in images)


def test_inference_settings_are_required(compose_config: dict[str, Any]) -> None:
    """A wrong embedding dimension is not detectable later; make it explicit.

    The embedding settings have no safe default and no way to be checked
    after the fact: a wrong dimension is accepted by everything and shows up
    as answers that cite nothing, long after the vectors were written. The
    chat endpoint is required for a different reason - a single-user stack
    started with nowhere to send a question is a mistake rather than a
    choice.
    """
    text = BASE.read_text()
    for variable in (
        "PRIMER_CHAT_BASE_URL",
        "PRIMER_EMBEDDING_BASE_URL",
        "PRIMER_EMBEDDING_MODEL",
        "PRIMER_EMBEDDING_DIMENSIONS",
    ):
        assert f"${{{variable}:?" in text


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
    """One plain URL: the services choose their driver, Retrieval passes it on as is."""
    users = database_users(compose_config)

    assert set(users) >= {"control", "chat", "retrieval", "worker", "migrate-vectors"}
    assert set(users.values()) == {"postgresql://primer:resolve-only@postgres:5432/primer"}
    assert "postgres" in compose_config["services"]


def test_your_own_postgresql_replaces_the_bundled_one() -> None:
    """No server started, nothing waiting on it, and no bundled password asked for."""
    if shutil.which("docker") is None:
        pytest.skip("docker is not installed")
    config = resolve(
        BASE, env={"PRIMER_DATABASE_URL": EXTERNAL_DATABASE_URL, "POSTGRES_PASSWORD": ""}
    )
    users = database_users(config)

    assert "postgres" not in config["services"]
    assert set(users.values()) == {EXTERNAL_DATABASE_URL}
    for migration in ("migrate-control", "migrate-chat", "migrate-vectors"):
        assert config["services"][migration]["depends_on"]["postgres"]["required"] is False
