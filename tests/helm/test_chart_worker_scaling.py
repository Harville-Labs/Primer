"""Workers scaled on their queues, and the parse worker's model threads.

Kept apart from `test_chart.py`, which is past the size a file should be.
"""

from __future__ import annotations

from typing import Any

from test_chart import containers, env_of, named, of_kind, refusal, render

SCALED = (
    "workers.parse.queueScaling.enabled=true",
    "workers.index.queueScaling.enabled=true",
)


def triggered_queues(scaled: dict[str, Any]) -> list[str]:
    return [trigger["metadata"]["queueName"] for trigger in scaled["spec"]["triggers"]]


def consumed_queues(deployment: dict[str, Any]) -> list[str]:
    command = containers(deployment)[0]["command"]
    return command[command.index("-Q") + 1].split(",")


def test_queue_scaling_is_off_unless_asked_for() -> None:
    """A ScaledObject on a cluster without KEDA fails the whole install."""
    manifests = render()

    assert of_kind(manifests, "ScaledObject") == []
    assert of_kind(manifests, "TriggerAuthentication") == []


def test_each_worker_scales_on_exactly_the_queues_it_consumes() -> None:
    """A trigger on a queue the worker does not read scales it for nothing,
    and a queue it reads without a trigger is a backlog nobody answers."""
    manifests = render(*SCALED)

    for worker in ("parse", "index"):
        scaled = named(manifests, "ScaledObject", f"-worker-{worker}")
        deployment = named(manifests, "Deployment", f"-worker-{worker}")
        assert scaled["spec"]["scaleTargetRef"]["name"] == deployment["metadata"]["name"]
        assert triggered_queues(scaled) == consumed_queues(deployment)


def test_a_scaled_worker_leaves_its_replica_count_to_keda() -> None:
    """A stated count is reapplied on every upgrade, and would shrink a
    worker KEDA had scaled up to the one in values in the middle of a
    backlog."""
    manifests = render("workers.index.queueScaling.enabled=true")

    assert "replicas" not in named(manifests, "Deployment", "-worker-index")["spec"]
    assert "replicas" in named(manifests, "Deployment", "-worker-parse")["spec"]


def test_the_broker_credential_comes_from_the_workers_own_secret() -> None:
    """One credential, not a copy of it for the autoscaler to fall out of date."""
    manifests = render(*SCALED)
    auth = of_kind(manifests, "TriggerAuthentication")
    assert len(auth) == 1

    (target,) = auth[0]["spec"]["secretTargetRef"]
    assert target == {"parameter": "host", "name": "primer-rabbitmq", "key": "broker-url"}
    for scaled in of_kind(manifests, "ScaledObject"):
        for trigger in scaled["spec"]["triggers"]:
            assert trigger["authenticationRef"]["name"] == auth[0]["metadata"]["name"]


def test_a_pod_is_added_when_the_running_ones_are_full() -> None:
    manifests = render(*SCALED)

    index = named(manifests, "ScaledObject", "-worker-index")
    assert {t["metadata"]["value"] for t in index["spec"]["triggers"]} == {"8"}


def test_the_model_cache_is_shared_when_keda_may_add_parse_pods() -> None:
    """Sized for the most pods KEDA may start, not the one running now.

    A ReadWriteOnce cache works until the second pod is scheduled on another
    node, and then that pod sits Pending in the middle of the backlog it was
    started to clear.
    """
    claim = named(render(SCALED[0]), "PersistentVolumeClaim", "-model-cache")
    assert claim["spec"]["accessModes"] == ["ReadWriteMany"]

    message = refusal(SCALED[0], "workers.parse.modelCache.accessMode=ReadWriteOnce")
    assert "ReadWriteOnce" in message


def test_a_worker_that_may_scale_to_one_gets_no_disruption_budget() -> None:
    """The budget follows the floor KEDA keeps, since that is what a drain
    can find running; a budget on a single pod makes it unevictable."""
    manifests = render(*SCALED, "workers.index.queueScaling.minReplicas=2")
    budgets = {doc["metadata"]["name"] for doc in of_kind(manifests, "PodDisruptionBudget")}

    assert any(name.endswith("-worker-index") for name in budgets)
    assert not any(name.endswith("-worker-parse") for name in budgets)


def test_docling_is_given_the_device_and_threads_from_values() -> None:
    env = env_of(
        named(
            render("workers.parse.accelerator.device=cuda", "workers.parse.accelerator.threads=2"),
            "Deployment",
            "-worker-parse",
        )
    )

    assert env["PRIMER_PARSE_DEVICE"] == "cuda"
    assert env["PRIMER_PARSE_THREADS"] == "2"
