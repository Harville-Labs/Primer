"""Lab configuration, validated once at startup.

Users choose the model Lab loads; the operator decides how far that choice
reaches. See ADR 0005.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from primer_contracts.lab import REPOSITORY_PATTERN, HubModel, LocalModel
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

QWEN_SCOPE_SAES = "Qwen/SAE-Res-Qwen3-1.7B-Base-W32K-L0_50"
QWEN_SCOPE_MODELS = ("Qwen/Qwen3-1.7B", "Qwen/Qwen3-1.7B-Base")
#: The transcoders circuit-tracer publishes for Qwen3-4B: one per layer,
#: 36 files and about 60 GB in all.
QWEN3_4B_TRANSCODERS = "mwhanna/qwen3-4b-transcoders"


class Settings(BaseSettings):
    """Deployment configuration for the Lab service."""

    #: Compose hands every optional Lab setting over as "" when it is unset,
    #: because it interpolates the whole file whichever profiles are on. An
    #: empty value therefore means "not set" here, as it does in Retrieval;
    #: read literally it would be an invalid model, device, or size.
    model_config = SettingsConfigDict(env_prefix="PRIMER_", extra="forbid", env_ignore_empty=True)

    #: Loaded at startup, before anyone has chosen. Set as JSON, for example
    #: PRIMER_LAB_MODEL='{"source": "local", "path": "my-finetune"}'.
    lab_model: HubModel | LocalModel = Field(
        default=HubModel(repository="Qwen/Qwen3-1.7B"), discriminator="source"
    )
    #: Who may switch the resident model. A switch affects every user of
    #: this Lab, so a deployment shared by a class may want it narrower.
    lab_model_choice: Literal["users", "admins", "operator"] = "users"
    admin_group: str | None = None
    #: Shell-style patterns a named repository must match. `Qwen/*` allows
    #: one publisher; the default allows any, because what makes a load
    #: safe is checked on the files, not taken from the name.
    lab_repository_patterns: tuple[str, ...] = ("*/*",)
    #: The largest set of weights Lab fetches, checked against the hub's
    #: file list before a byte is downloaded. It is also the operator's way
    #: of keeping a model off this hardware that the hardware cannot hold.
    lab_max_model_bytes: int = Field(default=20_000_000_000, ge=1)
    #: Sparse autoencoders to attach to a hub model, by the model's
    #: repository, fetched once the model has loaded. Set as JSON. The
    #: default is Qwen-Scope's for Qwen3-1.7B: trained on the base model,
    #: and published as working on its post-trained checkpoint too.
    lab_saes: dict[
        Annotated[str, Field(pattern=REPOSITORY_PATTERN)],
        Annotated[str, Field(pattern=REPOSITORY_PATTERN)],
    ] = Field(default_factory=lambda: dict.fromkeys(QWEN_SCOPE_MODELS, QWEN_SCOPE_SAES))
    #: Labels for SAE features, by the SAE repository they describe: a JSON
    #: Lines file in the weights volume, read at startup. See `labels`.
    lab_feature_labels: dict[Annotated[str, Field(pattern=REPOSITORY_PATTERN)], Path] = Field(
        default_factory=dict
    )
    #: Transcoders for attribution graphs, by the model's repository. Fetched
    #: once the model has loaded, and only where graphs can be built at all
    #: (`lab_graph_python`). A transcoder repository is also held to
    #: `lab_repository_patterns`, like a model. Set as JSON.
    lab_transcoders: dict[
        Annotated[str, Field(pattern=REPOSITORY_PATTERN)],
        Annotated[str, Field(pattern=REPOSITORY_PATTERN)],
    ] = Field(default_factory=lambda: {"Qwen/Qwen3-4B": QWEN3_4B_TRANSCODERS})
    #: Separate from the model limit, because a set of transcoders is
    #: several times the size of the model it explains.
    lab_max_transcoder_bytes: int = Field(default=70_000_000_000, ge=1)
    #: The interpreter of the graph builder's own environment. Unset, Lab
    #: builds no attribution graphs and fetches no transcoders. See ADR 0009.
    lab_graph_python: Path | None = None
    #: Bounds on one graph. A graph's cost grows with the prompt and the
    #: features kept, far faster than a forward pass's does.
    lab_graph_max_prompt_tokens: int = Field(default=64, ge=1, le=512)
    lab_graph_max_feature_nodes: int = Field(default=2048, ge=1, le=16384)
    #: The most edges a graph returns, strongest first, so a response stays
    #: something a browser can draw.
    lab_graph_max_edges: int = Field(default=2000, ge=1, le=20000)
    #: Held under five minutes, with the queue's grace, because that is how
    #: long the web server's fetch waits for Lab's response to begin. A
    #: longer limit would let Lab finish a graph nobody is waiting for. In
    #: practice that means graphs are built on a GPU.
    lab_graph_timeout_seconds: float = Field(default=240.0, gt=0, le=240)
    #: A mirror, for a deployment that cannot reach the public hub.
    lab_hub_endpoint: str | None = None
    lab_weights_dir: Path = Field(
        default=Path("/models"),
        description="Holds the operator's own directories and the fetch cache under hub/",
    )
    lab_device: Literal["auto", "cpu", "cuda"] = Field(
        default="auto", description="auto uses CUDA when torch can see a GPU"
    )
    lab_dtype: Literal["auto", "float32", "bfloat16", "float16"] = Field(
        default="auto", description="auto is bfloat16 on CUDA and float32 on CPU"
    )

    #: Bounds on a single run, enforced here rather than only in the form:
    #: the GPU is shared by everyone, and the form is not the only client.
    lab_max_prompt_tokens: int = Field(default=512, ge=1, le=8192)
    lab_max_new_tokens: int = Field(default=128, ge=1, le=2048)
    lab_max_interventions: int = Field(default=16, ge=0, le=256)
    lab_run_timeout_seconds: float = Field(default=120.0, gt=0)
    #: Runs allowed to wait behind the one in progress. Beyond this a run is
    #: refused at once, which is kinder than a wait nobody will sit through.
    lab_max_waiting_runs: int = Field(default=32, ge=0)

    #: Where saved experiments are kept. Without it Lab still runs, and
    #: refuses to save rather than keeping anything in memory that a user
    #: would believe was saved.
    database_url: str | None = Field(
        default=None,
        description="PostgreSQL URL; migrations are applied out of band, never at startup",
    )
    #: How long a saved experiment is kept, counted from when it was saved.
    #: An experiment holds the prompts it was made from, which may be private,
    #: so it is not kept indefinitely by default.
    lab_experiment_retention_days: int = Field(default=90, ge=1, le=3650)

    subject_header: str = Field(default="X-Forwarded-User")
    email_header: str = Field(default="X-Forwarded-Email")
    groups_header: str = Field(default="X-Forwarded-Groups")
    groups_delimiter: str = Field(default=",", min_length=1)
    auth_mode: str = Field(default="disabled")
