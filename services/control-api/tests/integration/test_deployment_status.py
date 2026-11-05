"""What the settings page may show, and to whom.

Two things are being guarded. The page exists so an operator can see what
Primer was pointed at without reading a values file and three sets of
container logs - and the addresses it shows routinely carry passwords,
because that is how PostgreSQL and RabbitMQ URLs are written.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

import pytest
from control_support import UserClient
from httpx2 import ASGITransport, AsyncClient
from primer_control.app import create_app
from primer_control.config import Settings
from primer_control.routes.admin import without_credentials
from primer_service.db import Database
from primer_storage import SourceStore
from sqlalchemy.ext.asyncio import AsyncEngine

ADMINS = "primer-admins"
ADMIN = {"X-Forwarded-User": "an-admin", "X-Forwarded-Groups": ADMINS}

type Build = Callable[..., AbstractAsyncContextManager[AsyncClient]]


class TestStrippingCredentialsFromAUrl:
    """Removed, not masked. A settings page is a thing people screenshot."""

    def test_a_password_is_removed(self) -> None:
        cleaned = without_credentials("postgresql://primer:hunter2@db:5432/primer") or ""

        assert "hunter2" not in cleaned
        assert cleaned == "postgresql://db:5432/primer"

    def test_the_username_goes_with_it(self) -> None:
        """It is half a credential, and it is not what an operator is checking."""
        assert without_credentials("amqp://user:pw@broker:5672/") == "amqp://broker:5672/"

    def test_nothing_is_starred_out(self) -> None:
        """A masked secret still says how long it was."""
        cleaned = (
            without_credentials("postgresql://primer:a-very-long-password@db:5432/primer") or ""
        )

        assert "*" not in cleaned
        assert "•" not in cleaned

    def test_a_url_with_no_credential_is_left_alone(self) -> None:
        assert without_credentials("http://chat:8000") == "http://chat:8000"

    def test_a_password_containing_an_at_sign_is_still_removed(self) -> None:
        """The host is after the last @, not the first."""
        cleaned = without_credentials("postgresql://primer:p@ss@db:5432/primer")

        assert cleaned == "postgresql://db:5432/primer"

    def test_nothing_configured_stays_nothing(self) -> None:
        assert without_credentials(None) is None


class TestWhenNobodyHasBeenNamedAnAdministrator:
    """Fails closed, which is the property worth pinning.

    The client these tests run against has authentication on and names no
    administrator group - the state a deployment is in before an operator
    has made the decision. Everyone is refused, rather than everyone being
    allowed to repoint the deployment.

    403 rather than 404: a settings page is restricted, not secret, and
    telling an ordinary user "not for you" is more useful than pretending
    the page does not exist.
    """

    async def test_an_authenticated_user_is_refused(self, owner: UserClient) -> None:
        response = await owner.get("/api/v1/admin/status")

        assert response.status_code == 403
        assert response.json()["code"] == "identity_invalid"

    async def test_the_refusal_says_why(self, owner: UserClient) -> None:
        assert "administrator" in (await owner.get("/api/v1/admin/status")).json()["detail"]


@pytest.fixture
def make_client(database: Database, clean_tables: AsyncEngine, source_store: SourceStore) -> Build:
    @asynccontextmanager
    async def build(**overrides: Any) -> AsyncIterator[AsyncClient]:
        settings = Settings(auth_mode="oidc", admin_group=ADMINS, **overrides)
        app = create_app(settings, database=database, source_store=source_store)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://control") as http:
            yield http

    return build


@pytest.mark.parametrize("vision", [True, False])
async def test_the_status_says_whether_a_vision_model_was_configured(
    make_client: Build, vision: bool
) -> None:
    """The settings page suggests a vision model from this, so it must not guess."""
    async with make_client(vision_enabled=vision) as http:
        response = await http.get("/api/v1/admin/status", headers=ADMIN)

    assert response.status_code == 200
    assert response.json()["vision_configured"] is vision


async def test_a_vision_model_without_studio_offers_no_studio_library_action(
    make_client: Build,
) -> None:
    """The chart tells Control about vision whether or not Studio is on.

    Studio's own switch is then the only thing between a deployment with a
    vision model and an action whose images live in a feature that is off.
    """
    async with make_client(vision_enabled=True, broker_url="amqp://broker.invalid") as http:
        response = await http.get("/api/v1/capabilities", headers=ADMIN)

    assert response.json()["studio_library_available"] is False
