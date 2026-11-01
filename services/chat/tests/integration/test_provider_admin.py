"""Managing providers, and who may.

These routes decide where every user's questions are sent and what
credential travels with them, so the tests that matter here are about
refusal: who is turned away, what is never returned, and what is refused
rather than done unsafely.
"""

from __future__ import annotations

import base64
import os
from typing import Any

import pytest
from chat_support import GroupedUser, deployment
from httpx2 import AsyncClient
from primer_chat.config import Settings

ADMINS = "primer-admins"


def a_key() -> str:
    return base64.b64encode(os.urandom(32)).decode()


@pytest.fixture
def settings() -> Settings:
    return Settings(
        auth_mode="oidc",
        admin_group=ADMINS,
        settings_encryption_key=a_key(),
    )


@pytest.fixture
def admin(client: AsyncClient) -> GroupedUser:
    return GroupedUser(client, "admin", ADMINS)


@pytest.fixture
def ordinary(client: AsyncClient) -> GroupedUser:
    return GroupedUser(client, "ordinary", "everyone")


async def add(user: GroupedUser, **fields: Any) -> Any:
    payload = {"name": "Workstation", "base_url": "http://127.0.0.1:9/v1", **fields}
    return await user.post("/api/v1/admin/providers", payload)


class TestWhoMayManageProviders:
    async def test_an_administrator_may(self, admin: GroupedUser) -> None:
        assert (await admin.get("/api/v1/admin/providers")).status_code == 200

    async def test_an_ordinary_user_may_not(self, ordinary: GroupedUser) -> None:
        """403, not 404. A settings page is restricted, not secret."""
        response = await ordinary.get("/api/v1/admin/providers")

        assert response.status_code == 403
        assert response.json()["code"] == "identity_invalid"

    async def test_an_ordinary_user_cannot_add_one(self, ordinary: GroupedUser) -> None:
        """The one that matters: this would repoint everyone's questions."""
        assert (await add(ordinary)).status_code == 403

    async def test_an_ordinary_user_cannot_delete_one(
        self, admin: GroupedUser, ordinary: GroupedUser
    ) -> None:
        created = (await add(admin)).json()
        response = await ordinary.delete(f"/api/v1/admin/providers/{created['id']}")

        assert response.status_code == 403
        assert (await admin.get("/api/v1/admin/providers")).json(), "it was deleted anyway"


class TestTheApiKey:
    async def test_it_is_never_returned(self, admin: GroupedUser) -> None:
        """A key a page can display is a key a screenshot can carry away."""
        created = (await add(admin, api_key="sk-super-secret-value")).json()

        assert "sk-super-secret-value" not in str(created)
        listed = (await admin.get("/api/v1/admin/providers")).json()
        assert "sk-super-secret-value" not in str(listed)

    async def test_holding_one_is_reported(self, admin: GroupedUser) -> None:
        """Whether a key is set is the part an operator needs to see."""
        with_key = (await add(admin, name="With", api_key="sk-value")).json()
        without = (await add(admin, name="Without")).json()

        assert with_key["api_key_set"] is True
        assert without["api_key_set"] is False

    async def test_it_can_be_removed(self, admin: GroupedUser) -> None:
        """An empty string is the third state, and the only way to unset."""
        created = (await add(admin, api_key="sk-value")).json()
        updated = await admin.patch(f"/api/v1/admin/providers/{created['id']}", {"api_key": ""})

        assert updated.json()["api_key_set"] is False

    async def test_it_survives_an_unrelated_edit(self, admin: GroupedUser) -> None:
        """Omitting the field leaves the stored key alone.

        Without this, renaming a provider would silently drop its
        credential - and the next question would fail for a reason nobody
        would connect to the rename.
        """
        created = (await add(admin, api_key="sk-value")).json()
        renamed = await admin.patch(f"/api/v1/admin/providers/{created['id']}", {"name": "Renamed"})

        assert renamed.json()["name"] == "Renamed"
        assert renamed.json()["api_key_set"] is True


class TestWithNoEncryptionKeyConfigured:
    """Storing a credential in the clear is not a fallback."""

    @pytest.fixture
    def settings(self) -> Settings:
        return deployment(admin_group=ADMINS)

    async def test_adding_a_provider_with_a_key_is_refused(self, admin: GroupedUser) -> None:
        response = await add(admin, api_key="sk-value")

        assert response.status_code == 422
        assert "encryption key" in response.json()["detail"]

    async def test_a_provider_without_a_key_is_still_allowed(self, admin: GroupedUser) -> None:
        """Most local servers need none, and should not be blocked by this."""
        assert (await add(admin)).status_code == 201


class TestTheDeploymentsOwnProvider:
    @pytest.fixture
    def settings(self) -> Settings:
        return Settings(
            auth_mode="oidc",
            admin_group=ADMINS,
            settings_encryption_key=a_key(),
            chat_base_url="http://configured:8000/v1",
        )

    async def test_it_is_listed_alongside_the_others(self, admin: GroupedUser) -> None:
        """One kind of thing to render, not a special case beside a general one."""
        await add(admin, name="Added")
        listed = (await admin.get("/api/v1/admin/providers")).json()

        sources = {entry["name"]: entry["source"] for entry in listed}
        assert sources["Added"] == "configured"
        assert "deployment" in sources.values()

    async def test_it_cannot_be_edited_here(self, admin: GroupedUser) -> None:
        """It lives in the environment; two places to change it could disagree."""
        listed = (await admin.get("/api/v1/admin/providers")).json()
        deployment = next(e for e in listed if e["source"] == "deployment")

        response = await admin.patch(
            f"/api/v1/admin/providers/{deployment['id']}", {"name": "Renamed"}
        )
        assert response.status_code == 409

    async def test_it_cannot_be_deleted_here(self, admin: GroupedUser) -> None:
        listed = (await admin.get("/api/v1/admin/providers")).json()
        deployment = next(e for e in listed if e["source"] == "deployment")

        assert (
            await admin.delete(f"/api/v1/admin/providers/{deployment['id']}")
        ).status_code == 409


class TestNaming:
    async def test_two_providers_cannot_share_a_name(self, admin: GroupedUser) -> None:
        """It is how a person tells two endpoints apart in the picker."""
        assert (await add(admin, name="Local")).status_code == 201
        clash = await add(admin, name="Local", base_url="http://elsewhere:9/v1")

        assert clash.status_code == 409
        assert clash.json()["code"] == "conflict"


class TestLogprobDeclarations:
    """What the scoring route trusts instead of probing, so it must be kept exactly."""

    async def test_neither_is_assumed(self, admin: GroupedUser) -> None:
        created = (await add(admin)).json()

        assert created["supports_logprobs"] is False
        assert created["supports_prompt_logprobs"] is False

    async def test_each_is_declared_and_changed_on_its_own(self, admin: GroupedUser) -> None:
        """Generated logprobs are common and prompt logprobs are not, so they differ often."""
        created = (await add(admin, supports_logprobs=True)).json()
        assert created["supports_logprobs"] is True
        assert created["supports_prompt_logprobs"] is False

        await admin.patch(
            f"/api/v1/admin/providers/{created['id']}", {"supports_prompt_logprobs": True}
        )
        await admin.patch(f"/api/v1/admin/providers/{created['id']}", {"name": "Renamed"})
        listed = (await admin.get("/api/v1/admin/providers")).json()
        stored = next(entry for entry in listed if entry["id"] == created["id"])

        assert stored["supports_logprobs"] is True
        assert stored["supports_prompt_logprobs"] is True
