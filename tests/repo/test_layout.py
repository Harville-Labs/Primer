"""Repository policy checks for the Primer monorepo toolchain."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]


def _root_pyproject() -> dict[str, Any]:
    return tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())


def test_python_runtime_is_pinned_to_3_13() -> None:
    assert (REPO_ROOT / ".python-version").read_text().strip() == "3.13"


def test_project_requires_python_3_13() -> None:
    config = _root_pyproject()
    assert config["project"]["requires-python"] == ">=3.13"


def test_dev_group_provides_the_supported_toolchain() -> None:
    dev = " ".join(_root_pyproject()["dependency-groups"]["dev"])
    for tool in ("ty", "ruff", "pytest"):
        assert tool in dev


def test_unsupported_type_checkers_are_not_configured() -> None:
    config = _root_pyproject()
    tools = config.get("tool", {})
    assert "mypy" not in tools
    assert "pyright" not in tools
    dev = " ".join(config["dependency-groups"]["dev"])
    assert "mypy" not in dev
    assert "pyright" not in dev


def test_workspace_members_cover_packages_and_services() -> None:
    members = _root_pyproject()["tool"]["uv"]["workspace"]["members"]
    assert "packages/*" in members
    assert "services/*" in members


def test_agent_planning_docs_are_untracked_by_policy() -> None:
    """Superpowers specs/plans stay local; user and API docs are tracked."""
    ignored = [
        line.strip().rstrip("/") for line in (REPO_ROOT / ".gitignore").read_text().splitlines()
    ]
    assert "docs/superpowers" in ignored
    assert "docs" not in ignored


def test_the_web_app_s_types_are_generated_rather_than_transcribed() -> None:
    """A contract change the web app has not followed should not compile.

    The schemas under `schemas/` are what the types are generated from, and
    CI regenerates both and diffs them. Asserting the wiring here means the
    guard cannot be quietly removed - a hand-written `interface
    LibrarySummary` would pass every other test in this repository.
    """
    types = (REPO_ROOT / "apps" / "web" / "src" / "lib" / "api" / "types.ts").read_text()

    assert "./generated/control" in types
    assert "./generated/chat" in types
    for shape in ("LibrarySummary", "DocumentSummary", "MessageSummary", "ConversationSummary"):
        assert f"export type {shape} = " in types, shape
        assert f"export interface {shape}" not in types, shape


def test_the_identity_boundary_is_still_written_by_hand() -> None:
    """Generation stops at the shapes.

    Which headers this server forwards to Primer is the thing that decides
    who a request is from. It is reviewed, not emitted by a tool from a
    document describing something else.
    """
    server = REPO_ROOT / "apps" / "web" / "src" / "lib" / "server"
    forwarding = (server / "forward.ts").read_text()

    assert forwarding.count("x-forwarded-user") == 1
    assert "generated" not in forwarding
    # And every client for a service goes through it, rather than one of
    # them growing its own list of headers that the others do not have.
    for client in ("api.ts", "chat.ts", "lab.ts"):
        source = (server / client).read_text()
        assert "forwardingIdentity(" in source, client
        assert "x-forwarded-user" not in source, client


def test_the_schemas_are_written_deterministically() -> None:
    """Or the check that they have not drifted fails on dict ordering."""
    script = (REPO_ROOT / "scripts" / "dump_openapi.py").read_text()

    assert "sort_keys=True" in script


def test_every_locked_image_copies_every_workspace_member() -> None:
    """`uv sync --locked` checks the lock against the whole workspace.

    An image that installs three services still has to carry the fourth's
    pyproject.toml, or uv sees a workspace that no longer matches the lock
    and refuses to build. Each new service has to be added to every image,
    and this is where forgetting that fails, rather than in CI's image job.
    """
    members = sorted(path.parent for path in REPO_ROOT.glob("services/*/pyproject.toml"))
    images = [
        path
        for path in sorted((REPO_ROOT / "deploy/images").glob("Dockerfile.*"))
        if "uv sync --locked" in path.read_text()
    ]
    assert members
    assert images
    for image in images:
        copied = {
            line.split()[1].rstrip("/")
            for line in image.read_text().splitlines()
            if line.startswith("COPY ") and not line.startswith("COPY --from")
        }
        for member in members:
            name = member.relative_to(REPO_ROOT).as_posix()
            assert name in copied or f"{name}/pyproject.toml" in copied, (
                f"{image.name} does not copy {name}/pyproject.toml, so its locked sync fails."
            )
