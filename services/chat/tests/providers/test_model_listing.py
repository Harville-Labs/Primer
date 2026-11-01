"""What the model list says across several providers at once.

Two failures are guarded here, and they are opposites.

The first is silence about a broken provider. Primer used to answer an
unreachable endpoint with the name of its configured default, so the picker
showed a model, the deployment looked healthy, and the first sign of trouble
was a question that hung and then failed.

The second is over-reacting to one. A deployment holding a hosted API and a
workstation that happens to be asleep can still answer perfectly well, and an
interface that reported it as broken would be wrong in the other direction.
"Nothing is available" and "one of three is unavailable" need different
reactions, and the whole point of the catalog is keeping them apart.

Real sockets rather than patched clients: "unreachable" and "answered badly"
are different states, and only a real connection tells them apart.
"""

from __future__ import annotations

import dataclasses
import uuid

from primer_chat.model_catalog import catalog
from primer_chat.providers_store import ResolvedProvider
from provider_stub import StubProvider


def provider_at(port: int | None, name: str = "Local") -> ResolvedProvider:
    return ResolvedProvider(
        id=uuid.uuid4(),
        name=name,
        base_url=f"http://127.0.0.1:{port}/v1" if port else "http://127.0.0.1:1/v1",
        api_key=None,
        source="configured",
    )


async def a_closed_port() -> int:
    """A port that is real and certainly closed, rather than guessed at."""
    async with StubProvider() as stub:
        return stub.port


async def test_a_reachable_provider_offers_what_it_serves() -> None:
    async with StubProvider(models=["alpha", "beta"]) as stub:
        listed = await catalog([provider_at(stub.port)], preferred_model="alpha")

    assert listed.endpoint_reachable
    assert [model.id for model in listed.models] == ["alpha", "beta"]
    assert [model.id for model in listed.models if model.default] == ["alpha"]


async def test_every_model_says_which_provider_serves_it() -> None:
    """Model names are not unique across providers, so the pair is the name."""
    async with (
        StubProvider(models=["llama3.1:8b"]) as first,
        StubProvider(models=["llama3.1:8b"]) as second,
    ):
        listed = await catalog(
            [provider_at(first.port, "Workstation"), provider_at(second.port, "Server")],
            preferred_model=None,
        )

    assert [model.provider_name for model in listed.models] == ["Workstation", "Server"]
    assert len({model.provider_id for model in listed.models}) == 2


async def test_every_model_carries_what_its_provider_declared() -> None:
    """A user who cannot read the provider list still needs to know what can be scored."""
    async with (
        StubProvider(models=["alpha"]) as first,
        StubProvider(models=["beta"]) as second,
    ):
        scoring = dataclasses.replace(
            provider_at(first.port, "Scoring"),
            supports_logprobs=True,
            supports_prompt_logprobs=True,
        )
        listed = await catalog([scoring, provider_at(second.port, "Plain")], preferred_model=None)

    declared = [(m.id, m.supports_logprobs, m.supports_prompt_logprobs) for m in listed.models]
    assert declared == [("alpha", True, True), ("beta", False, False)]


async def test_one_sleeping_provider_does_not_break_the_others() -> None:
    """The deployment can still answer, and says which half it cannot use."""
    closed = await a_closed_port()
    async with StubProvider(models=["alpha"]) as stub:
        listed = await catalog(
            [provider_at(stub.port, "Awake"), provider_at(closed, "Asleep")],
            preferred_model=None,
        )

    assert listed.endpoint_reachable
    assert [model.id for model in listed.models] == ["alpha"]
    assert listed.unreachable == ("Asleep",)


async def test_all_providers_unreachable_offers_nothing_and_says_so() -> None:
    """The regression: no phantom model, and a reason a person can act on."""
    closed = await a_closed_port()
    listed = await catalog([provider_at(closed, "Only")], preferred_model="configured-default")

    assert listed.models == ()
    assert not listed.endpoint_reachable
    assert listed.detail
    assert listed.unreachable == ("Only",)


async def test_no_providers_at_all_is_reported_as_such() -> None:
    listed = await catalog([], preferred_model=None)

    assert listed.models == ()
    assert not listed.endpoint_reachable
    assert listed.detail


async def test_a_preferred_model_nothing_serves_is_not_made_the_default() -> None:
    """A configured default naming a removed model is the same lie, smaller.

    Something actually served becomes the default rather than a name nothing
    answers to.
    """
    async with StubProvider(models=["actually-served"]) as stub:
        listed = await catalog([provider_at(stub.port)], preferred_model="removed-model")

    assert [model.id for model in listed.models if model.default] == ["actually-served"]


async def test_a_provider_serving_nothing_is_not_the_same_as_an_absent_one() -> None:
    """Up with no model loaded is a different problem, and a different fix."""
    async with StubProvider(models=[]) as stub:
        listed = await catalog([provider_at(stub.port, "Empty")], preferred_model=None)

    assert listed.models == ()
    assert listed.unreachable == ("Empty",)


async def test_a_provider_whose_key_cannot_be_read_is_not_called() -> None:
    """It would go out unauthenticated and fail for the wrong reason.

    The endpoint is real and an operator has to see it to fix it, so it is
    listed as unreachable with the key's own complaint rather than dropped.
    """
    async with StubProvider(models=["alpha"]) as stub:
        broken = ResolvedProvider(
            id=uuid.uuid4(),
            name="Rotated",
            base_url=f"http://127.0.0.1:{stub.port}/v1",
            api_key=None,
            source="configured",
            key_error="A stored API key could not be decrypted.",
        )
        listed = await catalog([broken], preferred_model=None)

    assert stub.requests == 0, "the endpoint was called despite an unusable key"
    assert listed.unreachable == ("Rotated",)
