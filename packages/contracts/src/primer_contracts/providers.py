"""Inference providers a deployment can answer from.

Primer ships no model and is not tied to one endpoint. An operator may run a
vLLM on their own hardware, keep an Ollama on a workstation, and hold an
account with a hosted API, and there is no reason a user should have to pick
between those at deploy time: they are different models with different costs
and different strengths, and which one suits a question is a decision that
belongs at the moment the question is asked.

So providers are a list, not a setting. Everything here follows from that.

The one configured in the chart or the environment is in that list too,
reported as `deployment` rather than kept somewhere separate. A deployment
that has never opened the settings page still has exactly one provider and
behaves as it always did, and the interface has one kind of thing to show
rather than a special case beside a general one.

A provider may also make images. That is declared on the provider rather
than kept as a second list, because a hosted account usually serves both
and one key should be entered once; see `primer_contracts.images`.

No API key ever appears here. `api_key_set` is the only thing said about it,
because a key that a page can display is a key that a screenshot, a cache, or
a browser extension can carry away.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from primer_contracts.base import WireModel
from primer_contracts.images import ImageCapabilities

ProviderName = Annotated[str, Field(min_length=1, max_length=80)]

#: Where a provider's configuration lives, which decides whether it can be
#: edited. `deployment` comes from the chart or the environment and is
#: changed by redeploying; `configured` was added through the settings page
#: and lives in Primer's own storage.
ProviderSource = Literal["deployment", "configured"]


class ProviderSummary(WireModel):
    """One endpoint Primer can ask, as an administrator sees it."""

    id: UUID
    #: The operator's own label - "Workstation Ollama", "OpenAI" - and what a
    #: user sees beside a model name. Not derived from the URL: two providers
    #: on the same host are a normal thing to want, and a hostname does not
    #: say which is the one with the big GPU.
    name: ProviderName
    base_url: str
    enabled: bool = True
    #: Whether a key is held, never the key. A provider that needs one and
    #: does not have it is a deployment misconfiguration worth showing.
    api_key_set: bool = False
    source: ProviderSource = "configured"
    #: What the operator has said this endpoint returns. Declared, never
    #: probed: an endpoint that ignores the parameter answers as though it had
    #: honoured it, so asking once would prove nothing about the next model.
    supports_logprobs: bool = False
    supports_prompt_logprobs: bool = False
    #: Whether its models are offered for questions. Off for an endpoint
    #: that only makes images, whose `/models` would otherwise put an image
    #: model in the chat picker.
    serves_chat: bool = True
    #: What it does with images, or null when it makes none.
    images: ImageCapabilities | None = None
    #: Null for the deployment's own provider, which has no stored row.
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ProviderCreate(WireModel):
    """A provider being added through the settings page."""

    name: ProviderName
    base_url: str = Field(min_length=1, max_length=2000)
    #: Write-only. Absent means no key, which is what most local servers
    #: want; they ignore it but require the header to exist.
    api_key: str | None = Field(default=None, max_length=500)
    enabled: bool = True
    #: Returns `logprobs` and `top_logprobs` on a chat completion. Most
    #: OpenAI-compatible servers do; some hosted APIs do not.
    supports_logprobs: bool = False
    #: Scores supplied text: returns logprobs for the prompt it was sent,
    #: through `echo` on `/completions`. vLLM does; few others do.
    supports_prompt_logprobs: bool = False
    serves_chat: bool = True
    images: ImageCapabilities | None = None

    @model_validator(mode="after")
    def _serves_something(self) -> Self:
        if not self.serves_chat and self.images is None:
            raise ValueError("a provider must serve chat, make images, or both")
        return self

    @field_validator("base_url")
    @classmethod
    def _normalize(cls, value: str) -> str:
        """Trim trailing slashes and nothing else.

        Provider paths differ - Ollama and llama.cpp serve at `/v1`, some
        gateways at a prefix of their own - so only what is unambiguous is
        normalized. Rewriting the path would break every deployment whose
        URL was already right.
        """
        trimmed = value.strip().rstrip("/")
        if not trimmed:
            raise ValueError("base_url must not be empty")
        return trimmed


class ProviderUpdate(WireModel):
    """A change to a provider. Absent fields are left alone.

    `api_key` has three states rather than two, which is why it is not a
    plain optional string: absent leaves the stored key untouched, a string
    replaces it, and an empty string removes it. Without the third, a key
    could be set and never unset.

    `images` has three as well, told apart by whether the field was sent:
    absent leaves the declaration alone, null withdraws it, and a value
    replaces it whole.
    """

    name: ProviderName | None = None
    base_url: str | None = Field(default=None, max_length=2000)
    api_key: str | None = Field(default=None, max_length=500)
    enabled: bool | None = None
    supports_logprobs: bool | None = None
    supports_prompt_logprobs: bool | None = None
    serves_chat: bool | None = None
    images: ImageCapabilities | None = None


class ProviderCheck(WireModel):
    """What happened when Primer asked a provider what it serves.

    Run on demand from the settings page, because the useful moment to learn
    that a URL is wrong is while it is still on screen being typed.
    """

    ok: bool
    detail: str = Field(max_length=500)
    #: What it serves, when it answered. Empty on failure, and empty is also
    #: a real answer from a server with no model loaded.
    models: tuple[str, ...] = ()
