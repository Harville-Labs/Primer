"""Static assertions over the rendered Helm chart.

These render the templates and read the result, rather than deploying
anything. What they guard is the set of mistakes that stay invisible until
someone is already relying on the deployment: a service reachable that
should not be, an identity header believed that should have been stripped, a
secret written into a manifest.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

CHART = Path(__file__).resolve().parents[2] / "deploy" / "helm" / "primer"

#: Enough to satisfy every `required` in the chart. None is a real value.
BASE_VALUES = [
    "postgresql.existingSecret=primer-postgres",
    "rabbitmq.existingSecret=primer-rabbitmq",
    "auth.oidc.issuerUrl=https://idp.example.com/",
    "auth.oidc.clientId=primer",
    "auth.oidc.existingSecret=primer-oidc",
    "inference.chat.baseUrl=http://model/v1",
    "inference.chat.model=chat-model",
    "ingress.host=primer.example.com",
]


def render(*overrides: str) -> list[dict[str, Any]]:
    if shutil.which("helm") is None:
        pytest.skip("helm is not installed")
    command = ["helm", "template", "primer", str(CHART)]
    for value in [*BASE_VALUES, *overrides]:
        command += ["--set", value]
    result = subprocess.run(command, capture_output=True, text=True, check=False)  # noqa: S603
    if result.returncode != 0:
        pytest.fail(f"helm template failed:\n{result.stderr}")
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def refusal(*overrides: str) -> str:
    """The message from a render the chart is supposed to refuse."""
    if shutil.which("helm") is None:
        pytest.skip("helm is not installed")
    command = ["helm", "template", "primer", str(CHART)]
    for value in [*BASE_VALUES, *overrides]:
        command += ["--set", value]
    result = subprocess.run(command, capture_output=True, text=True, check=False)  # noqa: S603
    if result.returncode == 0:
        pytest.fail("the chart rendered a configuration it should have refused")
    return result.stderr


@pytest.fixture(scope="module")
def manifests() -> list[dict[str, Any]]:
    return render()


def of_kind(manifests: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [doc for doc in manifests if doc.get("kind") == kind]


def named(manifests: list[dict[str, Any]], kind: str, suffix: str) -> dict[str, Any]:
    for doc in of_kind(manifests, kind):
        if doc["metadata"]["name"].endswith(suffix):
            return doc
    pytest.fail(f"no {kind} ending in {suffix}")


def containers(deployment: dict[str, Any]) -> list[dict[str, Any]]:
    return deployment["spec"]["template"]["spec"]["containers"]


def test_no_service_is_exposed_outside_the_cluster(manifests: list[dict[str, Any]]) -> None:
    """Control trusts its identity headers.

    Anything that could reach it directly could set those headers itself and
    be any user, so every service is ClusterIP and the ingress is the only
    way in. This is the single most important assertion in this file.
    """
    services = of_kind(manifests, "Service")
    # Guard against a vacuous pass: rendering nothing would satisfy the loop.
    assert len(services) >= 4

    for service in services:
        assert service["spec"]["type"] == "ClusterIP", service["metadata"]["name"]


def test_the_ingress_strips_inbound_identity_headers(manifests: list[dict[str, Any]]) -> None:
    """A request arriving with its own X-Auth-Request-User must not keep it."""
    ingress = of_kind(manifests, "Ingress")[0]
    snippet = ingress["metadata"]["annotations"][
        "nginx.ingress.kubernetes.io/configuration-snippet"
    ]

    for header in (
        "X-Auth-Request-User",
        "X-Auth-Request-Email",
        "X-Auth-Request-Groups",
        "X-Forwarded-User",
    ):
        assert header in snippet, f"{header} is not stripped"
    assert "more_clear_input_headers" in snippet


def test_the_ingress_routes_only_to_the_authenticating_proxy(
    manifests: list[dict[str, Any]],
) -> None:
    """No path reaches Control, Chat, or Retrieval directly."""
    ingress = of_kind(manifests, "Ingress")[0]
    backends = {
        path["backend"]["service"]["name"]
        for rule in ingress["spec"]["rules"]
        for path in rule["http"]["paths"]
    }

    assert backends == {"primer-primer-auth"}


def test_streaming_is_not_buffered(manifests: list[dict[str, Any]]) -> None:
    """Buffering would hold every token until the answer finished."""
    annotations = of_kind(manifests, "Ingress")[0]["metadata"]["annotations"]
    assert annotations["nginx.ingress.kubernetes.io/proxy-buffering"] == "off"


def test_every_pod_runs_unprivileged(manifests: list[dict[str, Any]]) -> None:
    deployments = of_kind(manifests, "Deployment")
    assert len(deployments) >= 6

    for deployment in deployments:
        pod = deployment["spec"]["template"]["spec"]
        assert pod["securityContext"]["runAsNonRoot"] is True, deployment["metadata"]["name"]
        for container in containers(deployment):
            security = container["securityContext"]
            assert security["allowPrivilegeEscalation"] is False
            assert security["readOnlyRootFilesystem"] is True
            assert security["capabilities"]["drop"] == ["ALL"]


# Settings whose names end like a credential but hold a number a reader is
# meant to see. Named one by one rather than pattern-matched, so a real
# credential cannot slip in behind a suffix.
NOT_CREDENTIALS = frozenset({"PRIMER_CHAT_CHARACTERS_PER_TOKEN"})


def test_no_credential_is_written_into_a_manifest(manifests: list[dict[str, Any]]) -> None:
    """A value in a manifest is a value in `kubectl get deployment -o yaml`."""
    for deployment in of_kind(manifests, "Deployment"):
        for container in containers(deployment):
            for entry in container.get("env", []):
                if entry["name"] in NOT_CREDENTIALS:
                    continue
                if entry["name"].endswith(("_TOKEN", "_API_KEY", "_SECRET", "_URL")):
                    if entry["name"].endswith("_URL") and "value" in entry:
                        # Service URLs and model endpoints are not secrets.
                        continue
                    assert "value" not in entry, f"{entry['name']} is inlined"
                    assert "valueFrom" in entry


def test_the_database_url_comes_from_a_secret(manifests: list[dict[str, Any]]) -> None:
    control = named(manifests, "Deployment", "-control")
    entry = next(e for e in containers(control)[0]["env"] if e["name"] == "PRIMER_DATABASE_URL")

    assert entry["valueFrom"]["secretKeyRef"]["name"] == "primer-postgres"


def test_migrations_run_once_per_release_not_once_per_pod(
    manifests: list[dict[str, Any]],
) -> None:
    """Three replicas racing to alter one schema is what a hook avoids."""
    jobs = of_kind(manifests, "Job")
    assert len(jobs) == 2

    for job in jobs:
        annotations = job["metadata"]["annotations"]
        assert annotations["helm.sh/hook"] == "pre-install,pre-upgrade"
        assert job["spec"]["template"]["spec"]["restartPolicy"] == "Never"

    for deployment in of_kind(manifests, "Deployment"):
        assert "initContainers" not in deployment["spec"]["template"]["spec"]


def test_every_schema_the_deployment_needs_is_migrated(
    manifests: list[dict[str, Any]],
) -> None:
    """Control and Chat. Retrieval keeps nothing in PostgreSQL, so a job for
    it would migrate a schema nothing reads."""
    directories = {containers(job)[0]["workingDir"] for job in of_kind(manifests, "Job")}

    assert directories == {"/app/services/control-api", "/app/services/chat"}


def test_parsing_and_indexing_scale_separately(manifests: list[dict[str, Any]]) -> None:
    """Parsing is CPU-heavy and holds documents in memory; indexing waits on a
    network call. One deployment would make them share a bottleneck."""
    parse = named(manifests, "Deployment", "-worker-parse")
    index = named(manifests, "Deployment", "-worker-index")

    parse_queues = containers(parse)[0]["command"][-1]
    index_queues = containers(index)[0]["command"][-1]

    assert parse_queues == "ingestion.parse"
    assert "ingestion.embed" in index_queues
    assert "ingestion.parse" not in index_queues


def test_component_topology_spread_is_configurable_without_post_rendering() -> None:
    """A release can spread its own pods while each service stays independent."""
    rendered = render(
        "podTopologySpread.enabled=true",
        "podTopologySpread.whenUnsatisfiable=DoNotSchedule",
        "podTopologySpread.matchLabelKeys[0]=pod-template-hash",
    )
    control = named(rendered, "Deployment", "-control")
    index = named(rendered, "Deployment", "-worker-index")

    constraint = control["spec"]["template"]["spec"]["topologySpreadConstraints"][0]
    assert constraint["topologyKey"] == "kubernetes.io/hostname"
    assert constraint["whenUnsatisfiable"] == "DoNotSchedule"
    assert constraint["labelSelector"]["matchLabels"]["app.kubernetes.io/component"] == "control"
    assert constraint["matchLabelKeys"] == ["pod-template-hash"]
    assert (
        index["spec"]["template"]["spec"]["topologySpreadConstraints"][0]["labelSelector"][
            "matchLabels"
        ]["app.kubernetes.io/component"]
        == "worker-index"
    )


def test_topology_spread_is_off_by_default(manifests: list[dict[str, Any]]) -> None:
    """A hard hostname rule would make the default chart unschedulable on one node."""
    for deployment in of_kind(manifests, "Deployment"):
        assert "topologySpreadConstraints" not in deployment["spec"]["template"]["spec"]


def test_the_parse_worker_keeps_its_model_cache(manifests: list[dict[str, Any]]) -> None:
    """Without it every pod restart re-downloads half a gigabyte."""
    claims = of_kind(manifests, "PersistentVolumeClaim")
    assert len(claims) == 1

    parse = named(manifests, "Deployment", "-worker-parse")
    mounts = {m["mountPath"] for m in containers(parse)[0]["volumeMounts"]}
    assert "/var/cache/primer/huggingface" in mounts


def test_scaling_the_parse_worker_is_all_it_takes(manifests: list[dict[str, Any]]) -> None:
    """The access mode follows the replica count rather than being chosen.

    Every parse replica mounts the one cache claim, so what the volume has
    to support is decided entirely by how many replicas there are. Asking
    an operator to set both is asking them to keep two values in step, and
    the failure when they drift is silent: extra pods that can never attach
    anything and sit Pending with no explanation.
    """
    one = named(manifests, "PersistentVolumeClaim", "-model-cache")
    several = named(render("workers.parse.replicas=2"), "PersistentVolumeClaim", "-model-cache")

    assert one["spec"]["accessModes"] == ["ReadWriteOnce"]
    assert several["spec"]["accessModes"] == ["ReadWriteMany"]


def test_one_replica_is_not_put_on_a_shared_volume() -> None:
    """ReadWriteMany is not the better mode, only the one that scales.

    Longhorn and its like serve it through an NFS share-manager pod, so
    defaulting to it would add a second workload and a network hop for a
    volume exactly one pod opens - and would fail outright on the block
    storage behind EBS, GCE PD, Azure Disk and local-path, which is what a
    chart installed on k3s or kind is sitting on.
    """
    claim = named(render("workers.parse.replicas=1"), "PersistentVolumeClaim", "-model-cache")

    assert claim["spec"]["accessModes"] == ["ReadWriteOnce"]


def test_several_replicas_on_a_single_node_volume_are_refused() -> None:
    """Only reachable by asking for the contradiction outright.

    Deriving the mode means nobody arrives here by omission, but someone
    who sets ReadWriteOnce and scales anyway has asked for a deployment
    that installs cleanly and does not run.
    """
    message = refusal(
        "workers.parse.replicas=2", "workers.parse.modelCache.accessMode=ReadWriteOnce"
    )

    # The way out is named, not just the problem.
    assert "Leave accessMode unset" in message
    assert "modelCache.enabled" in message


def test_a_shared_cache_rolls_without_going_to_zero() -> None:
    """Recreate exists only because a ReadWriteOnce volume cannot be held
    by the outgoing pod and a surge pod at once. A shared cache has no such
    problem, and keeping Recreate would take parsing to zero on every
    release for nothing.
    """
    parse = named(render("workers.parse.replicas=2"), "Deployment", "-worker-parse")

    assert parse["spec"]["replicas"] == 2
    assert parse["spec"].get("strategy", {}).get("type") != "Recreate"


def test_a_cache_that_only_one_node_can_hold_still_rolls_by_recreating(
    manifests: list[dict[str, Any]],
) -> None:
    """The default, and the reason the strategy is conditional at all."""
    parse = named(manifests, "Deployment", "-worker-parse")

    assert parse["spec"]["strategy"]["type"] == "Recreate"


def test_scaling_the_parse_worker_without_a_cache_is_allowed() -> None:
    """Each pod re-downloads the models, which is a cost and not a fault."""
    rendered = render("workers.parse.replicas=2", "workers.parse.modelCache.enabled=false")

    assert not [
        claim
        for claim in of_kind(rendered, "PersistentVolumeClaim")
        if claim["metadata"]["name"].endswith("-model-cache")
    ]


def test_liveness_and_readiness_differ(manifests: list[dict[str, Any]]) -> None:
    """Restarting a pod because PostgreSQL blipped is when it helps least."""
    control = containers(named(manifests, "Deployment", "-control"))[0]

    assert control["livenessProbe"]["httpGet"]["path"] == "/health/live"
    assert control["readinessProbe"]["httpGet"]["path"] == "/health/ready"


def test_every_pod_gets_a_writable_home_on_a_read_only_root(
    manifests: list[dict[str, Any]],
) -> None:
    """Several libraries create a cache directory under $HOME on import."""
    for name in ("-control", "-chat", "-retrieval", "-worker-parse"):
        deployment = named(manifests, "Deployment", name)
        mounts = {m["mountPath"] for m in containers(deployment)[0]["volumeMounts"]}
        # Container paths, not host paths: the read-only root is the point.
        assert {"/tmp", "/home/primer"} <= mounts, name  # noqa: S108


def test_the_proxy_uses_pkce_and_secure_cookies(manifests: list[dict[str, Any]]) -> None:
    """A leaked authorization code should not be enough to finish a sign-in."""
    args = containers(named(manifests, "Deployment", "-auth"))[0]["args"]

    assert "--code-challenge-method=S256" in args
    assert "--cookie-secure=true" in args
    assert "--cookie-httponly=true" in args


def test_the_proxy_sets_the_headers_primer_reads(manifests: list[dict[str, Any]]) -> None:
    """--pass-user-headers, not --set-xauthrequest.

    The latter sets X-Auth-Request-* on oauth2-proxy's own /oauth2/auth
    response, for the nginx auth_request / Traefik ForwardAuth pattern this
    deployment does not use. In --upstream reverse-proxy mode it forwards
    nothing; --pass-user-headers is what actually reaches Primer.
    """
    args = containers(named(manifests, "Deployment", "-auth"))[0]["args"]
    assert "--pass-user-headers=true" in args
    assert "--set-xauthrequest=true" not in args


def test_disabling_auth_removes_the_proxy_entirely() -> None:
    """Not a proxy in a permissive mode: no proxy at all, so there is no
    half-configured edge that looks like it is authenticating."""
    manifests = render("auth.mode=disabled")
    names = [d["metadata"]["name"] for d in of_kind(manifests, "Deployment")]

    assert not any(name.endswith("-auth") for name in names)


def test_the_internal_token_is_generated_not_configured(
    manifests: list[dict[str, Any]],
) -> None:
    """Primer's own cluster-internal credential means nothing outside this
    release, so it is generated rather than asked of the operator."""
    secrets = of_kind(manifests, "Secret")
    assert len(secrets) == 1
    assert "internal-token" in secrets[0]["data"]


def workloads(manifests: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Every pod-bearing manifest, by name, whatever kind it is."""
    found: dict[str, dict[str, Any]] = {}
    for doc in manifests:
        if doc.get("kind") in ("Deployment", "StatefulSet", "Job"):
            found[doc["metadata"]["name"]] = doc
    return found


def secrets_mounted(workload: dict[str, Any]) -> set[str]:
    names = set()
    for container in containers(workload):
        for source in container.get("envFrom") or []:
            name = source.get("secretRef", {}).get("name")
            if name:
                names.add(name)
    return names


def test_object_storage_credentials_reach_only_what_opens_a_file() -> None:
    """Control and the ingestion workers, and nothing else.

    Retrieval, Chat and the web app never open a source object. Handing them
    the object store's credentials would widen what a compromise of any of
    them reaches, for no capability they use.
    """
    rendered = render("sourceStore.existingSecret=primer-object-store")

    holders = {
        name
        for name, workload in workloads(rendered).items()
        if "primer-object-store" in secrets_mounted(workload)
    }

    assert holders, "the source store secret reached nothing at all"
    assert all(name.endswith("-control") or "-worker-" in name for name in holders), (
        f"object storage credentials reached something that does not read the store: {holders}"
    )
    assert any(name.endswith("-control") for name in holders)
    assert sum("-worker-" in name for name in holders) >= 2, (
        "both ingestion workers open source objects: parse reads them and embed writes artifacts"
    )


def test_object_storage_credentials_are_absent_until_configured(
    manifests: list[dict[str, Any]],
) -> None:
    """No secret named, nothing mounted: the default chart references none."""
    for name, workload in workloads(manifests).items():
        assert secrets_mounted(workload) == set(), f"{name} mounts a secret nothing configured"


def test_extra_environment_reaches_primer_workloads() -> None:
    """The escape hatch, so a missing setting does not need a fork."""
    rendered = render(
        "extraEnv[0].name=HTTPS_PROXY", "extraEnv[0].value=http://proxy.internal:3128"
    )

    carrying = {
        name
        for name, workload in workloads(rendered).items()
        for container in containers(workload)
        if any(entry.get("name") == "HTTPS_PROXY" for entry in container.get("env") or [])
    }

    assert {"control", "chat", "retrieval"} <= {name.rsplit("-", 1)[-1] for name in carrying}


def env_of(deployment: dict[str, Any]) -> dict[str, str]:
    return {entry["name"]: entry.get("value", "") for entry in containers(deployment)[0]["env"]}


def test_the_context_window_is_configurable_without_a_fork(
    manifests: list[dict[str, Any]],
) -> None:
    """Every model has a different window, and the chart has to say so."""
    env = env_of(named(manifests, "Deployment", "-chat"))

    assert env["PRIMER_CHAT_CONTEXT_TOKENS"] == "8192"
    assert env["PRIMER_CHAT_REPLY_TOKENS"] == "1024"
    assert env["PRIMER_CHAT_HISTORY_MESSAGES"] == "20"
    # A conversation that outgrows the window is summarized rather than
    # forgotten, and an operator has to be able to say otherwise.
    assert env["PRIMER_CHAT_COMPACT_HISTORY"] == "true"
    # Nothing named, nothing claimed: an empty map would be a JSON literal
    # the service has to parse for no reason.
    assert "PRIMER_CHAT_MODEL_CONTEXT_TOKENS" not in env


def test_a_models_context_window_reaches_chat() -> None:
    """JSON to the service, so the chart has to encode it."""
    rendered = render("inference.chat.modelContextTokens.long-model=131072")
    env = env_of(named(rendered, "Deployment", "-chat"))

    assert json.loads(env["PRIMER_CHAT_MODEL_CONTEXT_TOKENS"]) == {"long-model": 131072}


# --- Isolation ----------------------------------------------------------


def component_of(workload: dict[str, Any]) -> str:
    return workload["spec"]["template"]["metadata"]["labels"]["app.kubernetes.io/component"]


def policies(manifests: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Every NetworkPolicy, keyed by the component it protects.

    Keyed by what it selects rather than by its name, because what matters
    about a policy is which pods it lands on. The floor - the one selecting
    everything - is keyed as "all".
    """
    found: dict[str, dict[str, Any]] = {}
    for policy in of_kind(manifests, "NetworkPolicy"):
        selector = policy["spec"]["podSelector"].get("matchLabels", {})
        found[selector.get("app.kubernetes.io/component", "all")] = policy
    return found


def allowed_into(policy: dict[str, Any]) -> set[str]:
    """The components a policy lets in, by label."""
    sources = set()
    for rule in policy["spec"].get("ingress") or []:
        for peer in rule.get("from") or []:
            labels = peer.get("podSelector", {}).get("matchLabels", {})
            if "app.kubernetes.io/component" in labels:
                sources.add(labels["app.kubernetes.io/component"])
            elif "namespaceSelector" in peer:
                sources.add("*")
    return sources


def test_nothing_is_reachable_until_something_allows_it(
    manifests: list[dict[str, Any]],
) -> None:
    """The floor. A component added later is closed until someone opens it."""
    floor = policies(manifests)["all"]

    assert floor["spec"]["policyTypes"] == ["Ingress"]
    # No `ingress` key at all is what makes it a deny: an empty list of rules
    # matches nothing, while a missing policyTypes would restrict nothing.
    assert "ingress" not in floor["spec"]
    assert "app.kubernetes.io/component" not in floor["spec"]["podSelector"]["matchLabels"]


def test_each_service_admits_exactly_what_calls_it(manifests: list[dict[str, Any]]) -> None:
    """The paths the services actually use, and no others.

    Written out rather than derived, so adding a dependency means declaring
    it here - which is the point. A path discovered in production because a
    connection was refused is the failure this is meant to prevent.
    """
    admits = {name: allowed_into(policy) for name, policy in policies(manifests).items()}

    assert admits["control"] == {"web", "chat", "worker-parse", "worker-index"}
    assert admits["chat"] == {"web"}
    assert admits["retrieval"] == {"chat", "worker-parse", "worker-index"}
    # Only the proxy reaches the web app: a pod that could reach it directly
    # would arrive with no session and be whatever headers it chose to send.
    assert admits["web"] == {"auth"}


def test_the_only_door_in_is_the_proxy(manifests: list[dict[str, Any]]) -> None:
    """And it is the one rule wider than the rest, because it has to be."""
    entry = policies(manifests)["auth"]

    assert allowed_into(entry) == {"*"}
    assert entry["spec"]["ingress"][0]["ports"] == [{"port": 4180, "protocol": "TCP"}]


def test_the_door_can_be_narrowed_to_one_namespace() -> None:
    """An operator who knows where their ingress controller runs can say so."""
    rendered = render(
        "networkPolicies.ingress[0].namespaceSelector.matchLabels.kubernetes\\.io/metadata\\.name=ingress-nginx"
    )

    peers = policies(rendered)["auth"]["spec"]["ingress"][0]["from"]

    assert peers == [
        {"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "ingress-nginx"}}}
    ]


def test_without_the_proxy_the_web_app_is_the_door() -> None:
    """Disabling auth removes the proxy, so the policy has to follow it."""
    rendered = render("auth.mode=disabled")
    admits = policies(rendered)

    assert "auth" not in admits
    assert allowed_into(admits["web"]) == {"*"}
    assert admits["web"]["spec"]["ingress"][0]["ports"] == [{"port": 3000, "protocol": "TCP"}]


def test_policies_can_be_turned_off_for_a_cluster_that_ignores_them() -> None:
    """They are accepted and silently unenforced without a CNI that reads them."""
    rendered = render("networkPolicies.enabled=false")

    assert of_kind(rendered, "NetworkPolicy") == []


def test_every_workload_runs_as_its_own_account(manifests: list[dict[str, Any]]) -> None:
    """Sharing the namespace default means sharing whatever it can do."""
    accounts = {doc["metadata"]["name"] for doc in of_kind(manifests, "ServiceAccount")}
    assert accounts, "no accounts were rendered"

    seen = set()
    for name, workload in workloads(manifests).items():
        account = workload["spec"]["template"]["spec"].get("serviceAccountName")
        assert account in accounts, f"{name} runs as an account this chart did not create"
        # The migration jobs are one component run three times, so they share
        # one account; nothing else may.
        if not name.rsplit("-", 1)[0].endswith("migrate"):
            assert account not in seen, f"{account} is shared"
            seen.add(account)


def test_no_workload_mounts_a_token_it_does_not_use(manifests: list[dict[str, Any]]) -> None:
    """Nothing here calls the Kubernetes API.

    A token mounted into a pod that never reads it is a credential sitting in
    a filesystem for whoever gets into the container next.
    """
    for name, workload in workloads(manifests).items():
        pod = workload["spec"]["template"]["spec"]
        assert pod.get("automountServiceAccountToken") is False, name

    for account in of_kind(manifests, "ServiceAccount"):
        assert account["automountServiceAccountToken"] is False, account["metadata"]["name"]


def test_an_account_can_carry_the_cloud_role_that_reads_the_bucket() -> None:
    """Per component, because only two workloads open a source object."""
    rendered = render(
        "serviceAccounts.perComponent.worker-parse.eks\\.amazonaws\\.com/role-arn=arn:aws:iam::1:role/sources"
    )

    annotated = {
        account["metadata"]["name"]: (account["metadata"].get("annotations") or {})
        for account in of_kind(rendered, "ServiceAccount")
    }

    role = "eks.amazonaws.com/role-arn"
    # Present, not sole: these accounts are Helm hooks and carry the
    # annotations that make them one. Asserting the whole map would make this
    # a test of how the accounts are installed rather than of who may read
    # the bucket.
    assert annotated["primer-primer-worker-parse"][role] == "arn:aws:iam::1:role/sources"
    assert role not in annotated["primer-primer-chat"]


def test_a_cluster_that_manages_its_own_accounts_can_say_so() -> None:
    """No accounts created, and the pods fall back to the namespace default."""
    rendered = render("serviceAccounts.create=false")

    assert of_kind(rendered, "ServiceAccount") == []
    for name, workload in workloads(rendered).items():
        pod = workload["spec"]["template"]["spec"]
        assert pod["serviceAccountName"] == "default", name
        # Still not mounted: the default account's token is the one worth
        # mounting least.
        assert pod["automountServiceAccountToken"] is False, name


def test_every_worker_is_told_how_many_processes_to_run(
    manifests: list[dict[str, Any]],
) -> None:
    """Celery sizes its prefork pool from the node otherwise.

    A worker scheduled onto a large node forks a child per CPU against a
    memory limit set for a couple of them, and is killed on startup - before
    doing any work, for a reason that has nothing to do with the work. The
    number belongs to the container, so the container has to state it.
    """
    workers = {
        name: workload for name, workload in workloads(manifests).items() if "-worker-" in name
    }
    assert workers, "no workers were rendered"

    for name, workload in workers.items():
        command = containers(workload)[0]["command"]
        assert "--concurrency" in command, name
        # A number, not an empty string: `--concurrency ""` is how a
        # required value that was set to nothing would reach Celery.
        assert int(command[command.index("--concurrency") + 1]) >= 1, name


def test_the_parse_worker_runs_one_document_at_a_time(
    manifests: list[dict[str, Any]],
) -> None:
    """It holds a whole document and Docling's models; two is two of both."""
    command = containers(named(manifests, "Deployment", "-worker-parse"))[0]["command"]

    assert command[command.index("--concurrency") + 1] == "1"


def test_no_relevance_floor_unless_one_is_measured() -> None:
    """The scale belongs to the scorer, so the chart must not guess.

    A default here would drop good passages on a deployment using one
    embedding model and admit everything on the next, and both failures are
    silent.
    """
    env = env_of(named(render(), "Deployment", "-retrieval"))

    assert "PRIMER_MIN_SCORE" not in env


def test_a_measured_floor_reaches_retrieval() -> None:
    rendered = render("inference.minScore=0.45")
    env = env_of(named(rendered, "Deployment", "-retrieval"))

    assert env["PRIMER_MIN_SCORE"] == "0.45"


# --- Chunking -----------------------------------------------------------
#
# The chart shipped for a long time without wiring any of this, so every
# Kubernetes deployment chunked on document structure alone. It looked like
# a working install: documents reached "ready" and searches returned hits.
# What gave it away was the citations, which quoted a couple of words each,
# because structural chunking merges no adjacent items and a form or a
# statement is a long list of short ones.


def test_the_parse_worker_is_told_the_embedding_model() -> None:
    """The whole bug, as one assertion.

    The worker finds the model's tokenizer from the name, which is what lets
    chunks be bounded by tokens and small pieces merged; without it the
    worker falls back to structure alone. Nothing downstream reports the
    difference, so this is the only place it can be caught.
    """
    rendered = render("inference.embeddings.model=qwen3-embedding:0.6b")
    env = env_of(named(rendered, "Deployment", "-worker-parse"))

    assert env["PRIMER_EMBEDDING_MODEL"] == "qwen3-embedding:0.6b"
    assert env["PRIMER_MAX_CHUNK_TOKENS"] == "512"


def test_the_chart_does_not_derive_a_tokenizer_itself() -> None:
    """The worker's table does that, and knows Ollama's names; a template cannot.

    A second derivation here is the copy that drifts: it would hand
    `nomic-embed-text` to the worker as a repository it cannot fetch.
    """
    rendered = render("inference.embeddings.model=nomic-embed-text")
    env = env_of(named(rendered, "Deployment", "-worker-parse"))

    assert "PRIMER_CHUNK_TOKENIZER" not in env


def test_a_tokenizer_can_be_named_when_the_model_does_not_name_one() -> None:
    """The way out for a hosted model, which publishes none."""
    rendered = render(
        "inference.embeddings.model=text-embedding-3-small",
        "ingestion.chunkTokenizer=BAAI/bge-m3",
        "ingestion.maxChunkTokens=1024",
    )
    env = env_of(named(rendered, "Deployment", "-worker-parse"))

    assert env["PRIMER_CHUNK_TOKENIZER"] == "BAAI/bge-m3"
    assert env["PRIMER_MAX_CHUNK_TOKENS"] == "1024"


def test_retrieval_takes_the_width_and_prompts_from_the_model() -> None:
    """Emitting a chart default would override the model's own values.

    The chart once defaulted the width to 1536, which was right for one
    hosted model and wrong for every self-hosted one.
    """
    rendered = render("inference.embeddings.model=Qwen/Qwen3-Embedding-0.6B")
    env = env_of(named(rendered, "Deployment", "-retrieval"))

    assert env["PRIMER_EMBEDDING_MODEL"] == "Qwen/Qwen3-Embedding-0.6B"
    assert "PRIMER_EMBEDDING_DIMENSIONS" not in env
    assert "PRIMER_EMBEDDING_QUERY_PREFIX" not in env
    assert "PRIMER_EMBEDDING_DOCUMENT_PREFIX" not in env


def test_an_unknown_model_can_be_described() -> None:
    rendered = render(
        "inference.embeddings.model=someone/their-own-finetune",
        "inference.embeddings.dimensions=1024",
        "inference.embeddings.queryPrefix=query: ",
        "inference.embeddings.documentPrefix=passage: ",
    )
    env = env_of(named(rendered, "Deployment", "-retrieval"))

    assert env["PRIMER_EMBEDDING_DIMENSIONS"] == "1024"
    assert env["PRIMER_EMBEDDING_QUERY_PREFIX"] == "query: "
    assert env["PRIMER_EMBEDDING_DOCUMENT_PREFIX"] == "passage: "


def test_the_parse_worker_is_told_when_to_join_fragments() -> None:
    """Structural splitting leaves values stranded from their labels.

    A form whose labels are recognised as headings chunks to one value
    apiece - "52,000.00" on its own - which answers nothing and scores
    against questions it cannot answer.
    """
    rendered = render("inference.embeddings.model=Qwen/Qwen3-Embedding-0.6B")
    env = env_of(named(rendered, "Deployment", "-worker-parse"))

    assert env["PRIMER_MIN_CHUNK_CHARS"] == "40"


def test_fragments_are_joined_even_without_a_tokenizer() -> None:
    """The case that needs it most, so it is the one that must not skip it.

    A hosted model with no tokenizer named falls back to structural
    chunking, which merges nothing at all and so produces the most
    fragments of any configuration.
    """
    rendered = render("inference.embeddings.model=text-embedding-3-small")
    env = env_of(named(rendered, "Deployment", "-worker-parse"))

    assert env["PRIMER_MIN_CHUNK_CHARS"] == "40"


def test_joining_fragments_can_be_switched_off() -> None:
    """Zero is off, and has to survive being rendered rather than read falsy."""
    rendered = render(
        "inference.embeddings.model=Qwen/Qwen3-Embedding-0.6B",
        "ingestion.minChunkChars=0",
    )
    env = env_of(named(rendered, "Deployment", "-worker-parse"))

    assert env["PRIMER_MIN_CHUNK_CHARS"] == "0"


def test_only_the_worker_that_chunks_downloads_a_tokenizer() -> None:
    """The index worker embeds what parse already split. It needs none."""
    rendered = render(
        "inference.embeddings.model=text-embedding-3-small",
        "ingestion.chunkTokenizer=BAAI/bge-m3",
    )
    env = env_of(named(rendered, "Deployment", "-worker-index"))

    assert "PRIMER_CHUNK_TOKENIZER" not in env
    assert "PRIMER_EMBEDDING_MODEL" not in env


def test_the_parse_worker_is_told_how_many_pictures_to_read() -> None:
    """Only the PDF pipeline does OCR of its own.

    Every other format is converted by one that has none, so a slide deck
    of pasted charts is read by this or not at all - and each picture costs
    a full pass, which is why there is a number rather than no bound.
    """
    env = env_of(named(render(), "Deployment", "-worker-parse"))

    assert int(env["PRIMER_MAX_PICTURES_PER_DOCUMENT"]) > 0


def test_picture_reading_can_be_turned_off_without_disabling_ocr() -> None:
    """A deployment that wants PDFs read but not every image in a deck."""
    rendered = render("ingestion.maxPicturesPerDocument=0")
    env = env_of(named(rendered, "Deployment", "-worker-parse"))

    assert env["PRIMER_MAX_PICTURES_PER_DOCUMENT"] == "0"


def test_only_the_worker_that_parses_reads_pictures() -> None:
    """The index worker embeds what parse produced. It opens no documents."""
    env = env_of(named(render(), "Deployment", "-worker-index"))

    assert "PRIMER_MAX_PICTURES_PER_DOCUMENT" not in env


# --- Staying up ---------------------------------------------------------


def budgets(manifests: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Every PodDisruptionBudget, keyed by the component it protects."""
    return {
        policy["spec"]["selector"]["matchLabels"]["app.kubernetes.io/component"]: policy
        for policy in of_kind(manifests, "PodDisruptionBudget")
    }


def test_a_drain_cannot_take_a_multi_replica_component_to_zero(
    manifests: list[dict[str, Any]],
) -> None:
    """A drain evicts as fast as the pods will go, and two is not many."""
    protected = budgets(manifests)

    assert set(protected) == {"control", "chat", "retrieval", "web", "auth"}
    for name, policy in protected.items():
        assert policy["spec"]["maxUnavailable"] == 1, name


def test_a_single_replica_component_gets_no_budget(manifests: list[dict[str, Any]]) -> None:
    """Any budget at all would make its one pod unevictable.

    A node drain that never finishes is a worse failure than the restart it
    was avoiding, and a component running one pod had already accepted that
    restart.
    """
    assert "worker-parse" not in budgets(manifests)
    assert "worker-index" not in budgets(manifests)


def test_a_worker_scaled_up_is_protected_like_anything_else() -> None:
    """The rule is the replica count, not which component it is.

    The shared cache the replicas need comes with them: the access mode
    follows the replica count, so scaling is the only thing stated here.
    """
    rendered = render("workers.parse.replicas=3")

    assert "worker-parse" in budgets(rendered)
    assert "worker-index" not in budgets(rendered)


def test_every_container_is_sized(manifests: list[dict[str, Any]]) -> None:
    """Including the proxy, which is the workload in front of every request."""
    for name, workload in workloads(manifests).items():
        for container in containers(workload):
            resources = container.get("resources") or {}
            assert resources.get("requests"), f"{name}/{container['name']} has no requests"
            assert resources.get("limits"), f"{name}/{container['name']} has no limits"


def test_autoscaling_is_off_unless_it_is_asked_for(manifests: list[dict[str, Any]]) -> None:
    """A chart that autoscaled by default would scale on a metric nobody chose."""
    assert of_kind(manifests, "HorizontalPodAutoscaler") == []


def test_one_service_can_be_autoscaled_without_the_others() -> None:
    rendered = render("autoscaling.chat.enabled=true")
    scalers = of_kind(rendered, "HorizontalPodAutoscaler")

    assert [scaler["metadata"]["name"] for scaler in scalers] == ["primer-primer-chat"]
    # Merged over the shared defaults, so turning one on does not mean
    # restating every threshold.
    assert scalers[0]["spec"]["minReplicas"] == 2
    assert scalers[0]["spec"]["maxReplicas"] == 6


def test_an_autoscaled_deployment_does_not_also_state_its_replicas() -> None:
    """Otherwise every upgrade writes the configured number back.

    The autoscaler then undoes it, which reads as a service that scales down
    for no reason a few seconds after every release.
    """
    rendered = render("autoscaling.chat.enabled=true")

    assert "replicas" not in named(rendered, "Deployment", "-chat")["spec"]
    assert named(rendered, "Deployment", "-control")["spec"]["replicas"] == 2


def test_an_autoscaled_component_is_protected_at_its_floor() -> None:
    """The configured count is not the count once an autoscaler owns it."""
    rendered = render("autoscaling.chat.enabled=true", "autoscaling.chat.minReplicas=1")

    assert "chat" not in budgets(rendered)
    assert "control" in budgets(rendered)


def test_the_workers_are_not_autoscaled_on_cpu() -> None:
    """Their work arrives on a queue, and CPU does not describe a queue.

    An embedding worker waits on a network call, so its CPU is near zero
    exactly when the backlog is longest: a CPU autoscaler would add pods
    while they compute and none while they fall behind.
    """
    rendered = render("autoscaling.enabled=true")
    scaled = {
        scaler["metadata"]["labels"]["app.kubernetes.io/component"]
        for scaler in of_kind(rendered, "HorizontalPodAutoscaler")
    }

    assert scaled == {"control", "chat", "retrieval", "web"}


def test_memory_is_not_a_scaling_signal_until_someone_measures_it() -> None:
    """A Python service that has grown does not give the memory back."""
    default = render("autoscaling.enabled=true")
    measured = render(
        "autoscaling.enabled=true", "autoscaling.targetMemoryUtilizationPercentage=80"
    )

    def metrics(rendered: list[dict[str, Any]]) -> set[str]:
        scaler = next(
            doc
            for doc in of_kind(rendered, "HorizontalPodAutoscaler")
            if doc["metadata"]["name"].endswith("-chat")
        )
        return {metric["resource"]["name"] for metric in scaler["spec"]["metrics"]}

    assert metrics(default) == {"cpu"}
    assert metrics(measured) == {"cpu", "memory"}
