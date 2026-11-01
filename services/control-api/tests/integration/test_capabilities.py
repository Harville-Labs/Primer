"""What the deployment reports about itself.

The interface hides what will not work based on this, so a wrong answer
means a user is offered a feature that fails when they use it.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from control_support import UserClient
from httpx2 import ASGITransport, AsyncClient
from primer_control.app import create_app
from primer_control.config import Settings
from primer_service.db import Database
from primer_storage import SourceStore
from sqlalchemy.ext.asyncio import AsyncEngine


@pytest_asyncio.fixture
async def make_client(database: Database, clean_tables: AsyncEngine, source_store: SourceStore):
    def build(
        broker_url: str | None = None,
        chat_service_url: str | None = None,
        lab_service_url: str | None = None,
        tools_enabled: bool = False,
    ) -> AsyncClient:
        """Spelled out rather than **kwargs, so the settings stay type-checked."""
        app = create_app(
            Settings(
                auth_mode="oidc",
                broker_url=broker_url,
                chat_service_url=chat_service_url,
                lab_service_url=lab_service_url,
                tools_enabled=tools_enabled,
            ),
            database=database,
            source_store=source_store,
        )
        return AsyncClient(transport=ASGITransport(app=app), base_url="http://control")

    return build


async def capabilities_for(client: AsyncClient) -> dict:
    return (await UserClient(client, "reader").get("/api/v1/capabilities")).json()


async def test_a_deployment_without_a_broker_says_it_cannot_ingest(make_client) -> None:
    """An upload would sit queued forever; the interface should say so."""
    async with make_client(broker_url=None) as client:
        assert (await capabilities_for(client))["ingestion_available"] is False


async def test_a_deployment_with_a_broker_can_ingest(make_client) -> None:
    async with make_client(broker_url="amqp://guest:guest@rabbit:5672//") as client:
        assert (await capabilities_for(client))["ingestion_available"] is True


async def test_chat_is_unavailable_until_it_is_configured(make_client) -> None:
    async with make_client() as client:
        assert (await capabilities_for(client))["chat_available"] is False
    async with make_client(chat_service_url="http://chat:8000") as client:
        assert (await capabilities_for(client))["chat_available"] is True


async def test_lab_is_unavailable_until_it_is_configured(make_client) -> None:
    """Lab is optional, so its section stays hidden on a deployment without one."""
    async with make_client() as client:
        assert (await capabilities_for(client))["lab_available"] is False
    async with make_client(lab_service_url="http://lab:8000") as client:
        assert (await capabilities_for(client))["lab_available"] is True


async def test_tools_are_off_by_default(make_client) -> None:
    """Deny by default: tool use is opt-in for the whole deployment."""
    async with make_client() as client:
        assert (await capabilities_for(client))["tools_available"] is False


async def test_authentication_state_is_reported_so_the_ui_can_warn(make_client) -> None:
    """The difference between a private notebook and an open one."""
    async with make_client() as client:
        assert (await capabilities_for(client))["auth_enabled"] is True


@pytest.mark.parametrize("extension", [".pdf", ".docx", ".pptx", ".md", ".txt"])
async def test_supported_formats_come_from_the_storage_layer(make_client, extension: str) -> None:
    """One list, so the interface cannot promise what the server rejects."""
    async with make_client() as client:
        assert extension in (await capabilities_for(client))["supported_extensions"]


async def test_capabilities_require_an_identity(client: AsyncClient) -> None:
    """It describes a deployment's configuration; anonymous callers get nothing."""
    assert (await client.get("/api/v1/capabilities")).status_code == 401
