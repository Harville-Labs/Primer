"""How a deployment is wired, as an administrator needs to see it.

Reported rather than inferred. An operator debugging a self-hosted install
otherwise has to read a values file and several sets of container logs to
learn what Primer was pointed at and which of it is answering.

No credential appears in any of these, not even masked: a URL carrying one
is reported with that part removed, because a masked secret still says how
long it was and a screenshot of a settings page is a thing people paste into
issues.
"""

from __future__ import annotations

from pydantic import Field

from primer_contracts.base import WireModel


class DependencyStatus(WireModel):
    """One thing this deployment talks to."""

    name: str = Field(min_length=1, max_length=64)
    #: Where it was configured to be, with any credential stripped. None for
    #: a check that has no address of its own.
    url: str | None = Field(default=None, max_length=2000)
    reachable: bool
    detail: str = Field(max_length=500)


class DeploymentStatus(WireModel):
    """The deployment's own wiring, and how much of it is answering."""

    #: `oidc` or `disabled`. Which it is changes what every other page means.
    auth_mode: str = Field(max_length=32)
    #: The group whose members may see this. Null with authentication on
    #: means nobody can, which is worth showing on the page that says so.
    admin_group: str | None = Field(default=None, max_length=200)
    dependencies: tuple[DependencyStatus, ...] = ()
    #: Whether ingestion was given a vision model for figures and images.
    #: Control is told by the same values that hand the parse worker its
    #: model, so this is the deployment's answer rather than a probe of it.
    vision_configured: bool = False
