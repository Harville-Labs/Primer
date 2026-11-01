"""Policy checks on the repository-root `compose.yaml`.

The root file only includes the stack in `deploy/compose`, so that
`docker compose up` works from a fresh clone. Starting it there must give the
same stack as starting `deploy/compose/compose.yaml` directly: the same
services, build contexts, and volumes, under the same project name.
"""

from __future__ import annotations

import shutil

import pytest
from test_config import BASE, COMPOSE_DIR, resolve

ROOT = COMPOSE_DIR.parents[1] / "compose.yaml"

#: Every combination of the optional profiles, with none first.
PROFILES = ("", "graph", "lab", "graph,lab")


@pytest.fixture(autouse=True)
def _docker() -> None:
    if shutil.which("docker") is None:
        pytest.skip("docker is not installed")


@pytest.mark.parametrize("profiles", PROFILES)
def test_the_root_file_resolves_to_the_same_stack(profiles: str) -> None:
    """Compared whole, not per field, so a path that resolves differently fails.

    Build contexts are relative to the included file, and that file's
    extension fields do not carry over through `include`, so only those are
    left out of the comparison.
    """
    env = {"COMPOSE_PROFILES": profiles}
    from_root = resolve(ROOT, env=env)
    direct = resolve(BASE, env=env)

    assert from_root["services"]
    assert from_root["services"] == direct["services"]
    assert from_root["volumes"] == direct["volumes"]


def test_the_root_file_keeps_the_project_name() -> None:
    """Volumes are named after the project.

    Left to the default, the project would be named after the checkout's
    directory, and a stack started from the root would come up with an empty
    database beside the one `deploy/compose` had created.
    """
    assert resolve(ROOT)["name"] == resolve(BASE)["name"] == "primer"


def test_the_gpu_override_applies_on_top_of_the_root_file() -> None:
    env = {"COMPOSE_PROFILES": "lab"}
    from_root = resolve(ROOT, COMPOSE_DIR / "compose.gpu.yaml", env=env)
    direct = resolve(BASE, COMPOSE_DIR / "compose.gpu.yaml", env=env)

    assert from_root["services"]["lab"]["build"]["target"] == "cuda"
    assert from_root["services"]["lab"] == direct["services"]["lab"]
