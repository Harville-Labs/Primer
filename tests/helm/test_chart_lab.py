"""Policy checks on Lab, the optional service that runs a model of its own.

Lab is the one part of Primer that loads code shaped by a user's choice of
model, so what these guard is how little it is given: no shared secret but
the database, nothing writable beyond its weights, and nobody but the web
app to talk to.
"""

from __future__ import annotations

from typing import Any

from test_chart import containers, env_of, named, of_kind, refusal, render
from test_chart_qdrant import admitted

ENABLED = ("lab.enabled=true",)
GRAPHS = ("lab.gpu=1", "lab.graphs.enabled=true")


def lab(rendered: list[dict[str, Any]]) -> dict[str, Any]:
    return named(rendered, "Deployment", "-lab")


def test_lab_is_off_unless_it_is_turned_on() -> None:
    """It needs a machine sized for a model, which a default install is not."""
    rendered = render()
    assert rendered
    for doc in rendered:
        labels = doc["metadata"].get("labels", {})
        assert "lab" not in doc["metadata"]["name"], doc["metadata"]["name"]
        assert labels.get("app.kubernetes.io/component") != "lab"


def test_lab_runs_unprivileged() -> None:
    deployment = lab(render(*ENABLED))
    pod = deployment["spec"]["template"]["spec"]

    assert pod["securityContext"]["runAsNonRoot"] is True
    security = containers(deployment)[0]["securityContext"]
    assert security["allowPrivilegeEscalation"] is False
    assert security["readOnlyRootFilesystem"] is True
    assert security["capabilities"]["drop"] == ["ALL"]


def test_lab_is_replaced_rather_than_rolled() -> None:
    """A second pod would need the volume and a second copy of the model."""
    spec = lab(render(*ENABLED))["spec"]

    assert spec["replicas"] == 1
    assert spec["strategy"] == {"type": "Recreate"}


def test_only_the_web_app_may_reach_lab() -> None:
    """Anything else in the cluster could otherwise use it as free compute."""
    policy = named(render(*ENABLED), "NetworkPolicy", "-allow-lab")
    assert admitted(policy) == {"web"}


def test_lab_writes_only_its_weights_and_scratch() -> None:
    rendered = render(*ENABLED)
    deployment = lab(rendered)
    mounts = {m["mountPath"]: m["name"] for m in containers(deployment)[0]["volumeMounts"]}

    assert set(mounts) == {"/models", "/tmp", "/home/primer"}  # noqa: S108
    volumes = {v["name"]: v for v in deployment["spec"]["template"]["spec"]["volumes"]}
    claim = volumes[mounts["/models"]]["persistentVolumeClaim"]["claimName"]
    assert named(rendered, "PersistentVolumeClaim", "-lab-models")["metadata"]["name"] == claim


def test_lab_is_given_the_database_and_no_other_shared_secret() -> None:
    """It keeps saved experiments (ADR 0008); a token it never uses is only exposure."""
    container = containers(lab(render(*ENABLED)))[0]

    assert "envFrom" not in container
    secrets = {entry["name"] for entry in container["env"] if "valueFrom" in entry}
    assert secrets == {"PRIMER_DATABASE_URL"}
    names = {entry["name"] for entry in container["env"]}
    assert not any(name.startswith(("PRIMER_INTERNAL", "PRIMER_SETTINGS")) for name in names)


def test_lab_migrates_its_own_schema_in_its_own_image() -> None:
    """Only Lab's image holds Lab's migrations; any other would fail the hook."""
    rendered = render(*ENABLED, "lab.gpu=1")
    job = named(rendered, "Job", "-migrate-lab")
    (container,) = containers(job)

    assert container["workingDir"] == "/app/services/lab"
    assert container["command"] == ["alembic", "upgrade", "head"]
    assert container["image"] == containers(lab(rendered))[0]["image"]
    assert job["metadata"]["annotations"]["helm.sh/hook"] == "pre-install,pre-upgrade"


def test_lab_and_its_migrations_share_the_graphs_image() -> None:
    """The suffix is decided once; a job on the plain CUDA image is a second Lab image to pull."""
    rendered = render(*ENABLED, *GRAPHS)
    (job,) = containers(named(rendered, "Job", "-migrate-lab"))

    assert job["image"].endswith("-graphs")
    assert job["image"] == containers(lab(rendered))[0]["image"]


def test_how_long_experiments_are_kept_reaches_lab() -> None:
    assert env_of(lab(render(*ENABLED)))["PRIMER_LAB_EXPERIMENT_RETENTION_DAYS"] == "90"
    changed = lab(render(*ENABLED, "lab.experimentRetentionDays=14"))
    assert env_of(changed)["PRIMER_LAB_EXPERIMENT_RETENTION_DAYS"] == "14"


def test_who_may_choose_a_model_reaches_lab() -> None:
    env = env_of(
        lab(render(*ENABLED, "lab.modelChoice=admins", "lab.repositoryPatterns[0]=Qwen/*"))
    )

    assert env["PRIMER_LAB_MODEL_CHOICE"] == "admins"
    assert env["PRIMER_LAB_REPOSITORY_PATTERNS"] == '["Qwen/*"]'
    assert env["PRIMER_LAB_MODEL"] == '{"repository":"Qwen/Qwen3-1.7B","source":"hub"}'


def test_the_default_model_is_given_its_saes_and_an_operator_can_turn_them_off() -> None:
    default = env_of(lab(render(*ENABLED)))["PRIMER_LAB_SAES"]
    assert '"Qwen/Qwen3-1.7B":"Qwen/SAE-Res-Qwen3-1.7B-Base-W32K-L0_50"' in default

    # `null` from --set deletes the key; Lab must still be told "none", since
    # an absent variable would mean its own default, which is Qwen-Scope.
    assert env_of(lab(render(*ENABLED, "lab.saes=null")))["PRIMER_LAB_SAES"] == "{}"


def test_feature_labels_are_passed_as_json_and_default_to_none() -> None:
    assert env_of(lab(render(*ENABLED)))["PRIMER_LAB_FEATURE_LABELS"] == "{}"

    mapped = render(*ENABLED, "lab.featureLabels.Qwen/SAE-Res-Qwen3-1\\.7B=labels/qwen.jsonl")
    assert env_of(lab(mapped))["PRIMER_LAB_FEATURE_LABELS"] == (
        '{"Qwen/SAE-Res-Qwen3-1.7B":"labels/qwen.jsonl"}'
    )


def test_a_gpu_switches_to_the_cuda_image_and_requests_the_device() -> None:
    container = containers(lab(render(*ENABLED, "lab.gpu=1")))[0]

    assert container["image"].endswith("-cuda")
    assert container["resources"]["limits"]["nvidia.com/gpu"] == 1
    assert container["resources"]["limits"]["memory"] == "12Gi"


def test_without_a_gpu_lab_asks_for_none() -> None:
    container = containers(lab(render(*ENABLED)))[0]

    assert not container["image"].endswith("-cuda")
    assert "nvidia.com/gpu" not in container["resources"]["limits"]


def test_lab_has_its_own_service_account() -> None:
    accounts = [doc["metadata"]["name"] for doc in of_kind(render(*ENABLED), "ServiceAccount")]
    assert any(name.endswith("-lab") for name in accounts)


def test_control_and_web_are_told_where_lab_is_only_when_it_is_on() -> None:
    """Control's capability report is what shows the Lab section, so it follows the switch."""
    off = render()
    for name in ("-control", "-web"):
        env = env_of(named(off, "Deployment", name))
        assert not {"PRIMER_LAB_SERVICE_URL", "PRIMER_LAB_URL"} & set(env), name

    on = render(*ENABLED)
    service = named(on, "Service", "-lab")["metadata"]["name"]
    assert env_of(named(on, "Deployment", "-control"))["PRIMER_LAB_SERVICE_URL"] == (
        f"http://{service}:8000"
    )
    assert env_of(named(on, "Deployment", "-web"))["PRIMER_LAB_URL"] == f"http://{service}:8000"


def test_graphs_are_off_and_lab_is_told_of_no_transcoders() -> None:
    """Without the graphs image there is no builder, so a 60 GB fetch would buy nothing."""
    env = env_of(lab(render(*ENABLED, "lab.gpu=1")))

    assert not {"PRIMER_LAB_TRANSCODERS", "PRIMER_LAB_MAX_TRANSCODER_BYTES"} & set(env)


def test_graphs_switch_to_their_image_and_pass_the_transcoders() -> None:
    container = containers(lab(render(*ENABLED, *GRAPHS)))[0]
    env = env_of(lab(render(*ENABLED, *GRAPHS)))

    assert container["image"].endswith("-graphs")
    assert container["resources"]["limits"]["nvidia.com/gpu"] == 1
    assert env["PRIMER_LAB_TRANSCODERS"] == '{"Qwen/Qwen3-4B":"mwhanna/qwen3-4b-transcoders"}'
    assert env["PRIMER_LAB_MAX_TRANSCODER_BYTES"] == "70000000000"


def test_graphs_without_a_gpu_are_refused() -> None:
    """A CPU build outlasts the response limit, so the chart refuses it at install."""
    assert "lab.graphs.enabled needs lab.gpu" in refusal(*ENABLED, "lab.graphs.enabled=true")
