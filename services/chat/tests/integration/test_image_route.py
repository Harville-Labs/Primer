"""Making images through a provider that declares them.

The backend is a real socket serving a canned answer, and records what it
was sent, so the refusals can be proved: a request for something the
operator did not declare must not reach the backend at all.
"""

from __future__ import annotations

import base64
import json
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from chat_support import ChatUser, GroupedUser
from httpx2 import AsyncClient, Response
from primer_chat.config import Settings
from primer_chat.providers_store import deployment_image_provider, deployment_provider
from primer_contracts.images import ImageCapabilities

PNG = b"\x89PNG\r\n\x1a\n not really, but bytes all the same"
ENCODED = base64.b64encode(PNG).decode()
SECRET = "sk-the-images-key"  # noqa: S105 - a fake the stub echoes back


@dataclass(frozen=True)
class Received:
    path: str
    content_type: str
    body: bytes
    authorization: str

    @property
    def json(self) -> dict[str, Any]:
        return json.loads(self.body)


class ImageEndpoint:
    """Answers every POST with one status and body, and keeps each request."""

    def __init__(self) -> None:
        self.status = 200
        self.body: Any = {"data": [{"b64_json": ENCODED}]}
        self.received: list[Received] = []
        endpoint = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", 0))
                endpoint.received.append(
                    Received(
                        path=self.path.removeprefix("/v1"),
                        content_type=self.headers.get("Content-Type", ""),
                        body=self.rfile.read(length),
                        authorization=self.headers.get("Authorization", ""),
                    )
                )
                body = json.dumps(endpoint.body).encode()
                self.send_response(endpoint.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:
                """Silence the default stderr access log."""

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}/v1"


@pytest.fixture
def endpoint() -> Iterator[ImageEndpoint]:
    stub = ImageEndpoint()
    thread = threading.Thread(target=stub.server.serve_forever, daemon=True)
    thread.start()
    yield stub
    stub.server.shutdown()
    stub.server.server_close()
    thread.join(timeout=5)


CAPABILITIES = ImageCapabilities(
    models=("flux",),
    operations=("generate", "edit", "inpaint"),
    sizes=("1024x1024",),
    max_images=2,
    ask_for_base64=True,
)


@pytest.fixture
def settings(endpoint: ImageEndpoint) -> Settings:
    """A chat endpoint, and an image server of its own beside it."""
    return Settings(
        auth_mode="oidc",
        chat_base_url="http://model.invalid/v1",
        chat_model="m",
        images_base_url=endpoint.url,
        images_api_key=SECRET,
        images_capabilities=CAPABILITIES,
    )


@pytest.fixture
def user(client: AsyncClient) -> ChatUser:
    return ChatUser(client, "researcher")


def provider_id(settings: Settings) -> str:
    provider = deployment_image_provider(settings)
    assert provider is not None
    return str(provider.id)


async def make(user: ChatUser, settings: Settings, **fields: Any) -> Response:
    payload = {
        "provider_id": fields.pop("provider_id", None) or provider_id(settings),
        "model": "flux",
        "operation": "generate",
        "prompt": "A heron in fog",
        "size": "1024x1024",
        **fields,
    }
    return await user.post("/api/v1/images", payload)


class TestMakingImages:
    async def test_a_generation_returns_the_backends_images(
        self, user: ChatUser, settings: Settings, endpoint: ImageEndpoint
    ) -> None:
        response = await make(user, settings, seed=7)

        assert response.status_code == 200, response.text
        assert response.json()["images"] == [ENCODED]
        [sent] = endpoint.received
        assert sent.path == "/images/generations"
        assert sent.authorization == f"Bearer {SECRET}"
        assert sent.json == {
            "model": "flux",
            "prompt": "A heron in fog",
            "n": 1,
            "size": "1024x1024",
            "seed": 7,
            "response_format": "b64_json",
        }

    async def test_an_edit_sends_the_image_as_a_file(
        self, user: ChatUser, settings: Settings, endpoint: ImageEndpoint
    ) -> None:
        response = await make(user, settings, operation="edit", image=ENCODED)

        assert response.status_code == 200, response.text
        [sent] = endpoint.received
        assert sent.path == "/images/edits"
        assert sent.content_type.startswith("multipart/form-data")
        assert PNG in sent.body
        assert b'name="mask"' not in sent.body

    async def test_an_inpaint_sends_the_mask_too(
        self, user: ChatUser, settings: Settings, endpoint: ImageEndpoint
    ) -> None:
        response = await make(user, settings, operation="inpaint", image=ENCODED, mask=ENCODED)

        assert response.status_code == 200, response.text
        [sent] = endpoint.received
        assert b'name="mask"' in sent.body

    async def test_more_images_than_asked_for_are_cut_to_the_count(
        self, user: ChatUser, settings: Settings, endpoint: ImageEndpoint
    ) -> None:
        endpoint.body = {"data": [{"b64_json": ENCODED}] * 3}

        response = await make(user, settings)

        assert len(response.json()["images"]) == 1


class TestWhatIsNotDeclared:
    @pytest.mark.parametrize(
        ("fields", "named"),
        [
            ({"model": "sdxl"}, "'sdxl'"),
            ({"size": "512x512"}, "512x512"),
            ({"count": 3}, "3 images at once"),
        ],
    )
    async def test_is_refused_without_calling_the_backend(
        self,
        user: ChatUser,
        settings: Settings,
        endpoint: ImageEndpoint,
        fields: dict[str, Any],
        named: str,
    ) -> None:
        response = await make(user, settings, **fields)

        assert response.status_code == 422
        assert named in response.json()["detail"]
        assert "PRIMER_IMAGES_CAPABILITIES" in response.json()["detail"]
        assert endpoint.received == []

    async def test_a_chat_provider_that_declares_no_images_is_not_found(
        self, user: ChatUser, settings: Settings, endpoint: ImageEndpoint
    ) -> None:
        chat = deployment_provider(settings)
        assert chat is not None

        response = await make(user, settings, provider_id=str(chat.id))

        assert response.status_code == 404
        assert endpoint.received == []


class TestWhenTheBackendFails:
    @pytest.mark.parametrize(
        ("status", "expected", "advice"),
        [
            (401, 502, "will not help"),
            (404, 502, "not served there"),
            (429, 503, "Try again shortly"),
            (500, 502, "may help"),
        ],
    )
    async def test_its_status_is_explained_without_quoting_its_body(
        self,
        user: ChatUser,
        settings: Settings,
        endpoint: ImageEndpoint,
        status: int,
        expected: int,
        advice: str,
    ) -> None:
        """The body may echo the key it refused, so it is never repeated."""
        endpoint.status = status
        endpoint.body = {"error": {"message": f"bad key {SECRET}"}}

        response = await make(user, settings)

        assert response.status_code == expected
        assert advice in response.json()["detail"]
        assert "This deployment's images" in response.json()["detail"]
        assert SECRET not in response.text

    async def test_links_instead_of_images_are_not_followed(
        self, user: ChatUser, settings: Settings, endpoint: ImageEndpoint
    ) -> None:
        endpoint.body = {"data": [{"url": "http://169.254.169.254/latest"}]}

        response = await make(user, settings)

        assert response.status_code == 502
        assert "ask_for_base64" in response.json()["detail"]
        assert len(endpoint.received) == 1

    async def test_an_image_that_is_not_base64_is_refused(
        self, user: ChatUser, settings: Settings, endpoint: ImageEndpoint
    ) -> None:
        endpoint.body = {"data": [{"b64_json": "not base64 at all!"}]}

        response = await make(user, settings)

        assert response.status_code == 502
        assert "not valid base64" in response.json()["detail"]


class TestAnImageOnlyProvider:
    async def test_is_listed_for_images(self, user: ChatUser, settings: Settings) -> None:
        listed = (await user.get("/api/v1/images/providers")).json()

        assert [entry["provider_id"] for entry in listed] == [provider_id(settings)]
        assert listed[0]["capabilities"]["models"] == ["flux"]
        assert "base_url" not in listed[0]

    async def test_cannot_be_asked_a_question(self, user: ChatUser, settings: Settings) -> None:
        """Its `/models` would list image models as though they could answer."""
        response = await user.post(
            "/api/v1/score",
            {"provider_id": provider_id(settings), "model": "flux", "mode": "text", "text": "x"},
        )

        assert response.status_code == 404


class TestOneServerDoingBoth:
    @pytest.fixture
    def settings(self, endpoint: ImageEndpoint) -> Settings:
        return Settings(
            auth_mode="oidc",
            chat_base_url=endpoint.url,
            chat_api_key="sk-chat",
            chat_model="m",
            images_capabilities=CAPABILITIES,
        )

    async def test_is_one_provider_using_the_chat_key(
        self, user: ChatUser, settings: Settings, endpoint: ImageEndpoint
    ) -> None:
        assert deployment_image_provider(settings) is None
        chat = deployment_provider(settings)
        assert chat is not None

        response = await make(user, settings, provider_id=str(chat.id))

        assert response.status_code == 200, response.text
        assert endpoint.received[0].authorization == "Bearer sk-chat"


class TestAProviderAddedInSettings:
    @pytest.fixture
    def settings(self) -> Settings:
        return Settings(
            auth_mode="oidc",
            admin_group="admins",
            settings_encryption_key=base64.b64encode(b"k" * 32).decode(),
        )

    async def test_makes_images_once_declared(
        self, client: AsyncClient, user: ChatUser, endpoint: ImageEndpoint
    ) -> None:
        admin = GroupedUser(client, "admin", "admins")
        created = await admin.post(
            "/api/v1/admin/providers",
            {
                "name": "Omni",
                "base_url": endpoint.url,
                "api_key": SECRET,
                "serves_chat": False,
                "images": CAPABILITIES.model_dump(mode="json"),
            },
        )
        assert created.status_code == 201, created.text

        response = await user.post(
            "/api/v1/images",
            {
                "provider_id": created.json()["id"],
                "model": "flux",
                "operation": "generate",
                "prompt": "A heron",
                "size": "1024x1024",
            },
        )

        assert response.status_code == 200, response.text
        assert endpoint.received[0].authorization == f"Bearer {SECRET}"

    async def test_its_refusal_points_at_settings(
        self, client: AsyncClient, user: ChatUser, endpoint: ImageEndpoint
    ) -> None:
        admin = GroupedUser(client, "admin", "admins")
        created = await admin.post(
            "/api/v1/admin/providers",
            {
                "name": "Omni",
                "base_url": endpoint.url,
                "images": CAPABILITIES.model_dump(mode="json"),
            },
        )

        response = await user.post(
            "/api/v1/images",
            {
                "provider_id": created.json()["id"],
                "model": "flux",
                "operation": "generate",
                "prompt": "A heron",
                "size": "256x256",
            },
        )

        assert response.status_code == 422
        assert "for it in settings" in response.json()["detail"]
        assert endpoint.received == []
