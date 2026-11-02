"""Running a ComfyUI template, against a ComfyUI faked over real sockets.

The fake records the workflow it was sent, so a test can prove that what a
user typed reached the bound inputs and nothing else, and it can be told to
drop its WebSocket partway through a run.
"""

from __future__ import annotations

import asyncio
import base64
import copy
import json
import socket
import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest
import uvicorn
from chat_support import ChatUser
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import JSONResponse, Response
from httpx2 import AsyncClient
from primer_chat.config import Settings
from primer_chat.providers_store import deployment_image_provider
from primer_contracts.images import ImageCapabilities

PNG = b"\x89PNG\r\n\x1a\n made by the fake"
ENCODED = base64.b64encode(PNG).decode()

WORKFLOW: dict[str, dict[str, Any]] = {
    "3": {"class_type": "KSampler", "inputs": {"seed": 1, "steps": 20, "model": ["4", 0]}},
    "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "flux.safetensors"}},
    "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 512}},
    "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["4", 1]}},
    "9": {"class_type": "SaveImage", "inputs": {"images": ["3", 0]}},
    "10": {"class_type": "LoadImage", "inputs": {"image": "example.png"}},
}

TEMPLATE = {
    "name": "flux-local",
    "operations": ["generate", "edit"],
    "workflow": WORKFLOW,
    "bindings": {
        "prompt": {"node": "6", "input": "text"},
        "seed": {"node": "3", "input": "seed"},
        "steps": {"node": "3", "input": "steps"},
        "width": {"node": "5", "input": "width"},
        "height": {"node": "5", "input": "height"},
        "image": {"node": "10", "input": "image"},
        "output": "9",
    },
}

CAPABILITIES = ImageCapabilities.model_validate(
    {"api": "comfyui", "sizes": ["1024x1024"], "templates": [TEMPLATE]}
)


class FakeComfy:
    """Enough of ComfyUI's API to run one prompt at a time."""

    def __init__(self) -> None:
        self.submitted: list[dict[str, Any]] = []
        self.uploads: list[bytes] = []
        self.node_errors: dict[str, Any] | None = None
        self.drop_after_progress = False
        self.keep_history = True
        self.history: dict[str, Any] = {}
        self.sockets: dict[str, WebSocket] = {}
        # Held so a run is not collected while it is still sending.
        self.runs: set[asyncio.Task[None]] = set()
        app = FastAPI()

        @app.post("/upload/image")
        async def upload(request: Request) -> dict[str, str]:
            form = await request.form()
            file = form["image"]
            assert not isinstance(file, str)
            self.uploads.append(await file.read())
            return {"name": file.filename or "", "subfolder": "", "type": "input"}

        @app.post("/prompt")
        async def prompt(request: Request) -> Response:
            body = await request.json()
            self.submitted.append(body["prompt"])
            if self.node_errors is not None:
                return JSONResponse(
                    {"error": {"message": "invalid"}, "node_errors": self.node_errors}, 400
                )
            prompt_id = f"run-{len(self.submitted)}"
            task = asyncio.create_task(self.run(prompt_id, body["client_id"]))
            self.runs.add(task)
            task.add_done_callback(self.runs.discard)
            return JSONResponse({"prompt_id": prompt_id, "number": 1, "node_errors": {}})

        @app.websocket("/ws")
        async def ws(websocket: WebSocket) -> None:
            await websocket.accept()
            self.sockets[websocket.query_params["clientId"]] = websocket
            await websocket.send_json({"type": "status", "data": {"status": {}}})
            try:
                while True:
                    await websocket.receive_text()
            except Exception:  # noqa: BLE001 - the client hung up, which ends the fake's socket
                return

        @app.get("/history/{prompt_id}")
        async def history(prompt_id: str) -> dict[str, Any]:
            return {prompt_id: self.history[prompt_id]} if prompt_id in self.history else {}

        @app.get("/view")
        async def view(filename: str, type: str) -> Response:
            assert type == "output"
            return Response(PNG, media_type="image/png")

        self.app = app

    async def run(self, prompt_id: str, client_id: str) -> None:
        for _ in range(50):
            if client_id in self.sockets:
                break
            await asyncio.sleep(0.01)
        websocket = self.sockets[client_id]
        await websocket.send_bytes(b"\x00\x00\x00\x01 a preview")
        await websocket.send_json({"type": "status", "data": {"prompt_id": "someone-else"}})
        for step in (1, 2):
            await websocket.send_json(
                {"type": "progress", "data": {"value": step, "max": 2, "prompt_id": prompt_id}}
            )
        if self.drop_after_progress:
            await websocket.close(code=1011)
            return
        if self.keep_history:
            self.history[prompt_id] = {
                "outputs": {
                    "9": {"images": [{"filename": "out.png", "subfolder": "", "type": "output"}]},
                    "12": {"images": [{"filename": "preview.png", "type": "temp"}]},
                },
            }
        await websocket.send_json(
            {"type": "executing", "data": {"node": None, "prompt_id": prompt_id}}
        )


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def comfy() -> Iterator[tuple[FakeComfy, str]]:
    fake = FakeComfy()
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(fake.app, port=port, log_level="error", lifespan="off"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline, "the fake ComfyUI did not start"
        time.sleep(0.01)
    yield fake, f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture
def settings(comfy: tuple[FakeComfy, str]) -> Settings:
    return Settings(
        auth_mode="oidc",
        chat_base_url="http://model.invalid/v1",
        images_base_url=comfy[1],
        images_capabilities=CAPABILITIES,
    )


@pytest.fixture
def fake(comfy: tuple[FakeComfy, str]) -> FakeComfy:
    return comfy[0]


@pytest.fixture
def user(client: AsyncClient) -> ChatUser:
    return ChatUser(client, "researcher")


def request_body(settings: Settings, **fields: Any) -> dict[str, Any]:
    provider = deployment_image_provider(settings)
    assert provider is not None
    return {
        "provider_id": str(provider.id),
        "model": "flux-local",
        "operation": "generate",
        "prompt": "A heron in fog",
        "size": "1024x1024",
        **fields,
    }


def events_of(body: str) -> list[dict[str, Any]]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in body.splitlines()
        if line.startswith("data: ")
    ]


class TestRunningATemplate:
    async def test_returns_the_output_nodes_image_and_the_seed_it_chose(
        self, user: ChatUser, settings: Settings, fake: FakeComfy
    ) -> None:
        response = await user.post("/api/v1/images", request_body(settings))

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["images"] == [ENCODED]
        [sent] = fake.submitted
        assert body["seed"] == sent["3"]["inputs"]["seed"]
        assert (sent["5"]["inputs"]["width"], sent["5"]["inputs"]["height"]) == (1024, 1024)

    async def test_a_prompt_containing_json_reaches_only_the_bound_input(
        self, user: ChatUser, settings: Settings, fake: FakeComfy
    ) -> None:
        """The workflow is filled by assignment, so text cannot become structure."""
        hostile = '", "class_type": "ExecuteCode"}, "99": {"class_type": "ExecuteCode'

        response = await user.post(
            "/api/v1/images", request_body(settings, prompt=hostile, seed=7, steps=4)
        )

        assert response.status_code == 200, response.text
        [sent] = fake.submitted
        expected = copy.deepcopy(WORKFLOW)
        expected["6"]["inputs"]["text"] = hostile
        expected["3"]["inputs"].update(seed=7, steps=4)
        expected["5"]["inputs"].update(width=1024, height=1024)
        assert sent == expected

    async def test_an_edit_uploads_the_image_and_binds_its_name(
        self, user: ChatUser, settings: Settings, fake: FakeComfy
    ) -> None:
        response = await user.post(
            "/api/v1/images", request_body(settings, operation="edit", image=ENCODED)
        )

        assert response.status_code == 200, response.text
        assert fake.uploads == [PNG]
        name = fake.submitted[0]["10"]["inputs"]["image"]
        assert name.startswith("primer-") and name != "example.png"

    async def test_progress_is_streamed_before_the_result(
        self, user: ChatUser, settings: Settings
    ) -> None:
        response = await user.post("/api/v1/images/stream", request_body(settings))

        assert response.status_code == 200
        events = events_of(response.text)
        assert [event["type"] for event in events] == ["progress", "progress", "done"]
        assert events[1] == {"id": 1, "type": "progress", "value": 2, "max": 2}
        assert events[2]["result"]["images"] == [ENCODED]


class TestWhenItGoesWrong:
    async def test_a_node_error_names_the_node_without_quoting_comfyui(
        self, user: ChatUser, settings: Settings, fake: FakeComfy
    ) -> None:
        fake.node_errors = {"4": {"errors": [{"message": "ckpt_name not in list"}]}}

        response = await user.post("/api/v1/images", request_body(settings))

        assert response.status_code == 502
        detail = response.json()["detail"]
        assert "'flux-local' at node 4" in detail
        assert "not in list" not in detail

    async def test_a_socket_that_drops_mid_run_is_worth_trying_again(
        self, user: ChatUser, settings: Settings, fake: FakeComfy
    ) -> None:
        fake.drop_after_progress = True

        response = await user.post("/api/v1/images/stream", request_body(settings))

        events = events_of(response.text)
        assert [event["type"] for event in events] == ["progress", "progress", "failed"]
        assert events[-1]["status"] == 503
        assert "try again" in events[-1]["detail"]

    async def test_an_unbound_value_is_refused_before_comfyui_is_asked(
        self, user: ChatUser, settings: Settings, fake: FakeComfy
    ) -> None:
        response = await user.post(
            "/api/v1/images", request_body(settings, negative_prompt="blurry")
        )

        assert response.status_code == 422
        assert "negative prompt" in response.json()["detail"]
        assert fake.submitted == []

    async def test_an_operation_the_template_lacks_is_refused(
        self, user: ChatUser, settings: Settings, fake: FakeComfy
    ) -> None:
        response = await user.post(
            "/api/v1/images",
            request_body(settings, operation="inpaint", image=ENCODED, mask=ENCODED),
        )

        assert response.status_code == 422
        assert fake.submitted == []
