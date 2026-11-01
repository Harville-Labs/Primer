"""Scoring text from a provider's logprobs.

The endpoint is a real socket serving canned bodies, and records what it
was sent. That is what lets the refusals be proved: a provider that has not
declared logprobs must not be asked at all, and only a recording can show
that nothing was asked.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from chat_support import ChatUser, GroupedUser
from httpx2 import AsyncClient, Response
from primer_chat.config import Settings
from primer_chat.providers_store import deployment_provider


class LogprobEndpoint:
    """Answers each path with the body set for it, and keeps every request."""

    def __init__(self) -> None:
        self.bodies: dict[str, Any] = {}
        self.received: list[tuple[str, dict[str, Any]]] = []
        endpoint = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", 0))
                path = self.path.removeprefix("/v1")
                endpoint.received.append((path, json.loads(self.rfile.read(length))))
                body = json.dumps(endpoint.bodies.get(path, {})).encode()
                self.send_response(200 if path in endpoint.bodies else 404)
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
def endpoint() -> Iterator[LogprobEndpoint]:
    stub = LogprobEndpoint()
    thread = threading.Thread(target=stub.server.serve_forever, daemon=True)
    thread.start()
    yield stub
    stub.server.shutdown()
    stub.server.server_close()
    thread.join(timeout=5)


def declared(
    endpoint: LogprobEndpoint, *, logprobs: bool = False, prompt: bool = False
) -> Settings:
    return Settings(
        auth_mode="oidc",
        chat_base_url=endpoint.url,
        chat_model="m",
        chat_supports_logprobs=logprobs,
        chat_supports_prompt_logprobs=prompt,
    )


@pytest.fixture
def user(client: AsyncClient) -> ChatUser:
    return ChatUser(client, "researcher")


async def score(user: ChatUser, settings: Settings, mode: str, text: str) -> Response:
    provider = deployment_provider(settings)
    assert provider is not None
    return await user.post(
        "/api/v1/score",
        {"provider_id": str(provider.id), "model": "m", "mode": mode, "text": text, "top_k": 3},
    )


def chat_completion(*, with_logprobs: bool) -> dict[str, Any]:
    tokens = [("Par", -0.1), ("is", -0.01), (".", -2.5)]
    logprobs = {
        "content": [
            {
                "token": token,
                "logprob": logprob,
                "top_logprobs": [
                    {"token": token, "logprob": logprob},
                    {"token": "x", "logprob": -3.0},
                    {"token": "y", "logprob": -4.0},
                ],
            }
            for token, logprob in tokens
        ]
    }
    return {
        "choices": [
            {
                "message": {"role": "assistant", "content": "Paris."},
                "logprobs": logprobs if with_logprobs else None,
            }
        ]
    }


#: An echoed prompt of three tokens, then the one generated token after it.
ECHOED = {
    "choices": [
        {
            "text": " The cat sat.",
            "logprobs": {
                "tokens": [" The", " cat", " sat", "."],
                "token_logprobs": [None, -5.0, -1.0, -0.2],
                "top_logprobs": [
                    None,
                    {" cat": -5.0, " dog": -1.0},
                    {" sat": -1.0, " ran": -2.0},
                    {".": -0.2},
                ],
            },
        }
    ],
    "usage": {"prompt_tokens": 3, "completion_tokens": 1},
}


class TestWithNothingDeclared:
    @pytest.fixture
    def settings(self, endpoint: LogprobEndpoint) -> Settings:
        return declared(endpoint)

    async def test_generating_is_refused_naming_the_flag(
        self, user: ChatUser, settings: Settings, endpoint: LogprobEndpoint
    ) -> None:
        """And the endpoint is never asked: its answer would have no logprobs to show."""
        endpoint.bodies["/chat/completions"] = chat_completion(with_logprobs=True)
        response = await score(user, settings, "generate", "Capital of France?")

        assert response.status_code == 422
        assert "PRIMER_CHAT_SUPPORTS_LOGPROBS" in response.json()["detail"]
        assert endpoint.received == []

    async def test_scoring_text_is_refused_naming_the_flag(
        self, user: ChatUser, settings: Settings, endpoint: LogprobEndpoint
    ) -> None:
        endpoint.bodies["/completions"] = ECHOED
        response = await score(user, settings, "text", " The cat sat")

        assert response.status_code == 422
        assert "PRIMER_CHAT_SUPPORTS_PROMPT_LOGPROBS" in response.json()["detail"]
        assert endpoint.received == []


class TestWithLogprobsDeclared:
    @pytest.fixture
    def settings(self, endpoint: LogprobEndpoint) -> Settings:
        return declared(endpoint, logprobs=True)

    async def test_an_answer_is_scored_from_its_logprobs(
        self, user: ChatUser, settings: Settings, endpoint: LogprobEndpoint
    ) -> None:
        endpoint.bodies["/chat/completions"] = chat_completion(with_logprobs=True)
        response = await score(user, settings, "generate", "Capital of France?")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["text"] == "Paris."
        assert [token["token"] for token in body["score"]["tokens"]] == ["Par", "is", "."]
        sent = endpoint.received[0][1]
        assert sent["logprobs"] is True
        assert sent["top_logprobs"] == 3

    async def test_its_entropy_is_labelled_as_top_k_only(
        self, user: ChatUser, settings: Settings, endpoint: LogprobEndpoint
    ) -> None:
        """A lower bound read as the model's uncertainty is the mistake to prevent."""
        endpoint.bodies["/chat/completions"] = chat_completion(with_logprobs=True)
        result = (await score(user, settings, "generate", "Capital of France?")).json()["score"]

        assert result["estimate"] == "truncated"
        assert result["top_k"] == 3
        assert result["tokens"]
        assert all(token["entropy_bits"] is None for token in result["tokens"])
        assert all(token["truncated_entropy_bits"] is not None for token in result["tokens"])

    async def test_logprobs_missing_despite_the_flag_name_provider_and_flag(
        self, user: ChatUser, settings: Settings, endpoint: LogprobEndpoint
    ) -> None:
        """Not a flat score of zeros, which is what a missing field would read as."""
        endpoint.bodies["/chat/completions"] = chat_completion(with_logprobs=False)
        response = await score(user, settings, "generate", "Capital of France?")

        assert response.status_code == 502
        detail = response.json()["detail"]
        assert "This deployment" in detail
        assert "supports_logprobs" in detail

    async def test_declaring_logprobs_does_not_allow_scoring_text(
        self, user: ChatUser, settings: Settings, endpoint: LogprobEndpoint
    ) -> None:
        """Generated logprobs are common; prompt logprobs are not. One is not the other."""
        endpoint.bodies["/completions"] = ECHOED
        response = await score(user, settings, "text", " The cat sat")

        assert response.status_code == 422
        assert "PRIMER_CHAT_SUPPORTS_PROMPT_LOGPROBS" in response.json()["detail"]
        assert endpoint.received == []


class TestWithPromptLogprobsDeclared:
    @pytest.fixture
    def settings(self, endpoint: LogprobEndpoint) -> Settings:
        return declared(endpoint, prompt=True)

    async def test_supplied_text_is_scored_from_its_echo(
        self, user: ChatUser, settings: Settings, endpoint: LogprobEndpoint
    ) -> None:
        """The first token, which nothing predicted, and the generated tail are left out."""
        endpoint.bodies["/completions"] = ECHOED
        response = await score(user, settings, "text", " The cat sat")

        assert response.status_code == 200, response.text
        body = response.json()
        assert [token["token"] for token in body["score"]["tokens"]] == [" cat", " sat"]
        assert body["score"]["estimate"] == "truncated"
        sent = endpoint.received[0][1]
        assert sent["echo"] is True
        assert sent["logprobs"] == 3

    async def test_the_text_is_sent_as_given(
        self, user: ChatUser, settings: Settings, endpoint: LogprobEndpoint
    ) -> None:
        """A leading space is a token of its own, so stripping it scores other text."""
        endpoint.bodies["/completions"] = ECHOED
        await score(user, settings, "text", " The cat sat")

        assert endpoint.received[0][1]["prompt"] == " The cat sat"

    async def test_prompt_logprobs_missing_despite_the_flag(
        self, user: ChatUser, settings: Settings, endpoint: LogprobEndpoint
    ) -> None:
        endpoint.bodies["/completions"] = {"choices": [{"text": " The cat sat."}]}
        response = await score(user, settings, "text", " The cat sat")

        assert response.status_code == 502
        assert "supports_prompt_logprobs" in response.json()["detail"]


class TestTheEndpointFailing:
    @pytest.fixture
    def settings(self, unused_tcp_port: int) -> Settings:
        return Settings(
            auth_mode="oidc",
            chat_base_url=f"http://127.0.0.1:{unused_tcp_port}/v1",
            chat_model="m",
            chat_supports_logprobs=True,
        )

    async def test_an_unreachable_one_is_named_and_worth_retrying(
        self, user: ChatUser, settings: Settings
    ) -> None:
        response = await score(user, settings, "generate", "Hello")

        assert response.status_code == 503
        assert "try again" in response.json()["detail"]


class TestAConfiguredProvider:
    @pytest.fixture
    def settings(self) -> Settings:
        return Settings(auth_mode="oidc", admin_group="admins")

    async def test_its_refusal_points_at_settings(
        self, client: AsyncClient, user: ChatUser, endpoint: LogprobEndpoint
    ) -> None:
        """Not at an environment variable, which would not change it."""
        admin = GroupedUser(client, "admin", "admins")
        created = await admin.post(
            "/api/v1/admin/providers", {"name": "Workstation", "base_url": endpoint.url}
        )
        response = await user.post(
            "/api/v1/score",
            {
                "provider_id": created.json()["id"],
                "model": "m",
                "mode": "generate",
                "text": "Hello",
            },
        )

        assert response.status_code == 422
        assert "declare supports_logprobs for it in settings" in response.json()["detail"]
        assert endpoint.received == []
