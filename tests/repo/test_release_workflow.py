"""Policy checks for publishing release artifacts."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_the_helm_registry_path_uses_a_lowercase_repository_owner() -> None:
    """OCI repository paths must be lowercase even when the GitHub owner is not."""
    workflow = (REPO_ROOT / ".github" / "workflows" / "release.yml").read_text()

    assert 'owner="${GITHUB_REPOSITORY_OWNER,,}"' in workflow
    assert "oci://ghcr.io/${owner}/charts" in workflow


def test_the_release_refuses_a_chart_whose_images_are_not_where_it_pushed_them() -> None:
    """Checking the tag alone passed 1.1.0, whose images defaulted to the old owner."""
    workflow = (REPO_ROOT / ".github" / "workflows" / "release.yml").read_text()

    assert 'grep -q "ghcr.io/${owner}/primer-api:${{ needs.version.outputs.version }}"' in workflow


def test_the_charts_default_images_are_under_the_owner_that_publishes_them() -> None:
    """The workflows push to the repository owner, Harville-Labs, lowercased."""
    values = (REPO_ROOT / "deploy" / "helm" / "primer" / "values.yaml").read_text()
    repositories = [
        line.split(":", 1)[1].strip()
        for line in values.splitlines()
        if line.strip().startswith("repository: ghcr.io/") and "/primer-" in line
    ]
    assert repositories, "no Primer images found in values.yaml, so this checks nothing"

    for repository in repositories:
        assert repository.startswith("ghcr.io/harville-labs/"), repository
