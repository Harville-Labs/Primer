"""What the deployment images may carry.

These read the Dockerfiles rather than build them: a build takes minutes
and every one of these mistakes is visible in the text.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
IMAGES = REPO_ROOT / "deploy" / "images"
PYTHON_IMAGES = ("Dockerfile.python", "Dockerfile.worker", "Dockerfile.lab")


def _stages(dockerfile: str) -> list[str]:
    """The Dockerfile split at each FROM, one string per stage."""
    text = (IMAGES / dockerfile).read_text()
    stages = re.split(r"^(?=FROM )", text, flags=re.MULTILINE)
    return [stage for stage in stages if stage.startswith("FROM")]


def _head(stage: str) -> tuple[str, str]:
    """A stage's base and its name; an unnamed stage is the default target."""
    words = stage.split("\n", 1)[0].split()
    return words[1], words[3] if len(words) > 3 else "default"


def test_no_image_installs_a_development_group() -> None:
    """`uv sync` installs the `dev` group unless told not to.

    Before this was written down, the API and Lab images carried
    testcontainers and the Docker SDK - a client for a socket no deployment
    should mount - because the syncs had no `--no-dev`.
    """
    syncs = [
        line
        for name in PYTHON_IMAGES
        for line in (IMAGES / name).read_text().splitlines()
        if "uv sync" in line and not line.lstrip().startswith("#")
    ]
    assert len(syncs) >= len(PYTHON_IMAGES), "found no syncs, so nothing was checked"
    for sync in syncs:
        assert "--no-dev" in sync, sync


def test_uv_and_compilers_stay_out_of_what_runs() -> None:
    """uv and gcc live in stages named `build*`, and no target descends from one.

    A published image is then a copy of what was built, not the builder,
    so it can neither install packages nor compile them.
    """
    for name in PYTHON_IMAGES:
        stages = [_head(stage) for stage in _stages(name)]
        bodies = dict(zip((alias for _, alias in stages), _stages(name), strict=True))
        builders = {alias for alias, body in bodies.items() if "/uv " in body or "gcc" in body}
        assert builders, f"{name} has no build stage, so nothing was checked"
        assert all(alias.startswith("build") for alias in builders), (name, builders)
        for base, alias in stages:
            if base.startswith("build"):
                assert alias.startswith("build"), f"{name}: {alias} is built on {base}"


def test_the_web_server_needs_no_installed_packages() -> None:
    """The web image ships the adapter's build and no node_modules.

    adapter-node bundles a package listed as a devDependency and leaves one
    listed as a dependency to be installed beside the build. The image
    installs nothing, so a dependency would build, pass every test, and then
    fail to import when the server starts.
    """
    package = json.loads((REPO_ROOT / "apps" / "web" / "package.json").read_text())
    assert package["devDependencies"], "found no packages, so nothing was checked"
    assert not package.get("dependencies"), (
        "list it under devDependencies: the web image has no node_modules"
    )
