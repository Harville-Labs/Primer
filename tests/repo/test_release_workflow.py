"""Policy checks for publishing release artifacts."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_the_helm_registry_path_uses_a_lowercase_repository_owner() -> None:
    """OCI repository paths must be lowercase even when the GitHub owner is not."""
    workflow = (REPO_ROOT / ".github" / "workflows" / "release.yml").read_text()

    assert 'owner="${GITHUB_REPOSITORY_OWNER,,}"' in workflow
    assert "oci://ghcr.io/${owner}/charts" in workflow
