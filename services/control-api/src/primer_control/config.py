"""Validated Control API settings.

Authentication is either delegated to a trusted edge (`oidc`) or replaced by
one fixed local identity (`disabled`). Primer never validates OIDC tokens
itself, so the only auth-related configuration here is which trusted headers
the edge injects.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

AuthMode = Literal["disabled", "oidc"]


class Settings(BaseSettings):
    """Deployment configuration for the Control API."""

    model_config = SettingsConfigDict(env_prefix="PRIMER_", extra="forbid")

    auth_mode: AuthMode = "disabled"

    database_url: str = Field(
        default="postgresql+asyncpg://primer:primer@localhost:5432/primer",
        description="PostgreSQL URL; migrations are applied out of band, never at startup",
    )

    source_store_url: str = Field(
        default="file:///var/lib/primer/sources",
        description="fsspec URL for uploaded source bytes; a local path or an object store",
    )
    max_upload_bytes: int = Field(
        default=100 * 1024 * 1024,
        ge=1,
        description="Largest single upload accepted, enforced while the stream is read",
    )
    upload_chunk_bytes: int = Field(
        default=1024 * 1024,
        ge=1,
        description="Read size for streaming uploads and downloads; bounds per-request memory",
    )

    broker_url: str | None = Field(
        default=None,
        description="RabbitMQ URL for ingestion work; unset leaves uploaded jobs queued",
    )

    chat_service_url: str | None = Field(
        default=None,
        description="Chat service URL; unset means this deployment has no chat",
    )
    lab_service_url: str | None = Field(
        default=None,
        description="Lab service URL; unset means this deployment has no Lab",
    )
    tools_enabled: bool = Field(
        default=False,
        description="Deployment-wide switch for MCP tools; off is the safe default",
    )

    studio_enabled: bool = Field(
        default=False,
        description="Deployment-wide switch for Studio; off until an operator has a policy for it",
    )
    studio_max_upload_bytes: int = Field(
        default=20 * 1024 * 1024,
        ge=1,
        le=25 * 1024 * 1024,
        description="Largest image a person may upload to edit, before it is decoded",
    )
    studio_max_side: int = Field(
        default=4096,
        ge=64,
        # 8192 squared is under Pillow's decompression-bomb threshold, so the
        # two limits never disagree about an image this one allows.
        le=8192,
        description="Widest or tallest image accepted or stored, in pixels",
    )
    studio_max_images_per_request: int = Field(default=4, ge=1, le=10)
    studio_generations_per_hour: int = Field(
        default=30, ge=1, description="Generations one person may start in an hour"
    )
    studio_quota_bytes: int = Field(
        default=1024 * 1024 * 1024,
        ge=1,
        description="Studio storage one person may use, uploads and results together",
    )
    studio_retention_days: int | None = Field(
        default=None,
        ge=1,
        description="Delete Studio images older than this; unset keeps them until deleted",
    )
    studio_timeout_seconds: float = Field(
        default=900.0,
        gt=0,
        description="How long Control waits for Chat to finish one generation",
    )

    internal_api_token: SecretStr | None = Field(
        default=None,
        description="Shared credential for the cluster-internal worker API; unset denies it",
    )
    job_lease_seconds: int = Field(
        default=300,
        ge=1,
        description="How long a claimed stage is held before another worker may re-claim it",
    )
    max_job_attempts: int = Field(
        default=5,
        ge=1,
        description="Hard retry bound, held here because a broker redelivery resets a worker's",
    )

    subject_header: str = Field(
        default="X-Forwarded-User",
        description="Edge-injected header carrying the stable OIDC subject",
    )
    email_header: str = Field(default="X-Forwarded-Email")
    groups_header: str = Field(default="X-Forwarded-Groups")
    groups_delimiter: str = Field(default=",", min_length=1)
    #: The group whose members may see and change how this deployment is
    #: wired. Unset with authentication on means nobody, which is the safe
    #: reading of an operator who has not decided yet.
    admin_group: str | None = Field(default=None)
    request_id_header: str = Field(default="X-Request-ID")

    @model_validator(mode="after")
    def _oidc_requires_configured_headers(self) -> Settings:
        """OIDC mode without a subject header would authenticate nobody.

        Failing at startup is the only safe outcome: the alternative is a
        deployment that believes it is authenticated but rejects every user,
        or worse, one that silently falls back to a local identity.
        """
        if self.auth_mode == "oidc" and not self.subject_header.strip():
            raise ValueError("auth_mode='oidc' requires a non-empty subject_header")
        return self

    @property
    def auth_enabled(self) -> bool:
        return self.auth_mode == "oidc"
