"""Chat configuration.

Chat holds its own database credentials and its own schema. It shares a
PostgreSQL instance with Control but not a migration history, so the two can
be released independently.
"""

from __future__ import annotations

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from primer_chat.budget import CHARACTERS_PER_TOKEN


class Settings(BaseSettings):
    """Deployment configuration for the Chat service."""

    model_config = SettingsConfigDict(env_prefix="PRIMER_", extra="forbid")

    database_url: str = Field(
        default="postgresql+asyncpg://primer:primer@localhost:5432/primer",
        description="PostgreSQL URL; migrations are applied out of band, never at startup",
    )

    control_url: str = Field(default="http://control-api:8000")
    retrieval_url: str = Field(default="http://retrieval:8000")
    service_token: SecretStr | None = Field(
        default=None,
        description="Credential presented to Control and Retrieval",
    )
    request_timeout_seconds: float = Field(default=30.0, gt=0)

    #: The endpoint configured for this deployment. Any OpenAI-compatible
    #: one: vLLM, Ollama, llama.cpp, or a hosted API. Primer ships no model
    #: of its own, and since a deployment may hold several providers at once
    #: this is a member of that list rather than the whole of it.
    chat_base_url: str | None = Field(default=None)
    #: Optional. Naming a model here only says which of the ones a provider
    #: serves should be offered first; it is not required, and a name nothing
    #: serves is ignored rather than offered. Primer asks each provider what
    #: it has, so a deployment that names nothing gets whatever is there.
    chat_model: str | None = Field(default=None)
    chat_api_key: SecretStr | None = Field(default=None)
    chat_timeout_seconds: float = Field(default=120.0, gt=0)
    #: What the deployment's endpoint returns, as its operator declares it.
    #: Never inferred from the model or the server: an endpoint that ignores
    #: `logprobs` answers normally, so a probe that got an answer proves
    #: nothing. Off unless said, which leaves anomaly scoring refused rather
    #: than wrong.
    chat_supports_logprobs: bool = Field(
        default=False,
        description="The endpoint returns logprobs and top_logprobs on a chat completion",
    )
    chat_supports_prompt_logprobs: bool = Field(
        default=False,
        description="The endpoint scores supplied text, through echo on /completions (vLLM)",
    )

    retrieval_limit: int = Field(
        default=8,
        ge=1,
        le=50,
        description="Passages retrieved per question; bounds prompt size and cost",
    )
    #: How much of a conversation the model is shown when answering the next
    #: question. Counted in messages rather than exchanges because that is
    #: what is sent, and bounded because every prior turn is paid for again
    #: on every turn after it. The oldest are dropped first: a conversation
    #: is understood from its recent turns, not its first ones.
    chat_history_messages: int = Field(
        default=20,
        ge=0,
        le=200,
        description="Prior messages replayed to the model; 0 answers each question alone",
    )
    #: The window a model has, and how much of it to leave for the answer.
    #: Stated rather than discovered: an OpenAI-compatible endpoint is not
    #: required to report its context length, several report one they do not
    #: honour, and being wrong about it means a refused request after the
    #: user has already waited.
    chat_context_tokens: int = Field(
        default=8192,
        ge=1024,
        description="Context window assumed for a model with no entry in chat_model_context_tokens",
    )
    #: Per model, for a deployment offering several. Set as JSON:
    #: PRIMER_CHAT_MODEL_CONTEXT_TOKENS='{"llama-3.1-8b-instruct": 131072}'
    chat_model_context_tokens: dict[str, int] = Field(default_factory=dict)
    chat_reply_tokens: int = Field(
        default=1024,
        ge=64,
        description="Held back from the window for the answer itself",
    )
    #: How pessimistically to estimate a prompt. Primer talks to any
    #: OpenAI-compatible endpoint, so it cannot hold the tokenizer of every
    #: model it might be pointed at; lower this for documents that tokenize
    #: worse than English prose - code, tables, non-Latin scripts.
    chat_characters_per_token: float = Field(default=CHARACTERS_PER_TOKEN, gt=0.5, le=8.0)
    #: Whether the turns that fall out of the window are summarized before
    #: they go. On by default: a long conversation otherwise loses its own
    #: beginning, and the thing a user mentioned once is exactly what a later
    #: question tends to depend on. It costs a model call whenever something
    #: is actually dropped, which is the reason it can be turned off.
    chat_compact_history: bool = Field(
        default=True,
        description="Summarize the turns that no longer fit instead of dropping them",
    )
    #: Room set aside for that summary, and the length the summarizer is held
    #: to. Reserved whenever compaction is on, whether or not a summary
    #: exists yet, so that writing one never costs the history a turn it was
    #: already shown.
    chat_summary_tokens: int = Field(
        default=512,
        ge=64,
        le=4096,
        description="Space reserved for the summary of compacted turns",
    )
    heartbeat_seconds: float = Field(
        default=15.0,
        gt=0,
        description="Idle keepalive, so a proxy does not close a slow stream",
    )

    def context_tokens(self, model: str | None) -> int:
        """The window to fit a prompt into, for whichever model will answer."""
        name = model or self.chat_model
        if name is None:
            return self.chat_context_tokens
        return self.chat_model_context_tokens.get(name, self.chat_context_tokens)

    def resolve_model(self, requested: str | None) -> str | None:
        """The model to use: what was asked for, or the configured default.

        Primer keeps no list of its own to check a name against - `/models`
        already told the caller everything the providers serve, so a name
        that reaches here came from that list. What an endpoint does with a
        name it no longer recognizes is the same failure as any other model
        error, handled where those already are.

        None when neither is set, which a deployment configuring no default
        is entitled to be: the caller picked from a list it was given, and a
        deployment need not have an opinion about what to do when nobody did.

        The trailing `or None` is what makes that true in practice. An unset
        environment variable arrives as an empty string rather than as None,
        and an empty string is a value: it flowed through the caller's
        `is not None` check as though a model had been named, and was sent to
        the endpoint, which answered `Model '' not found`. So a deployment
        that had configured no model got a 404 instead of the catalog default
        it was entitled to - and the fix looked like setting the variable,
        which is exactly the requirement this was meant to remove.
        """
        return requested or self.chat_model or None

    #: Encrypts API keys for providers added through the settings page. The
    #: chart generates it; without one, keys cannot be stored at all and the
    #: settings page says so rather than writing a credential in the clear.
    settings_encryption_key: SecretStr | None = Field(default=None)
    #: The group whose members may see and change how this deployment is
    #: wired. Unset with authentication on means nobody, which is the safe
    #: reading of an operator who has not decided yet.
    admin_group: str | None = Field(default=None)

    subject_header: str = Field(default="X-Forwarded-User")
    email_header: str = Field(default="X-Forwarded-Email")
    groups_header: str = Field(default="X-Forwarded-Groups")
    groups_delimiter: str = Field(default=",", min_length=1)
    auth_mode: str = Field(default="disabled")
