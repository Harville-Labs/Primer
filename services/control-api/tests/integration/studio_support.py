"""Fixtures for Studio: a Chat faked over real sockets, and images to send it.

Control calls Chat over HTTP, so the fake is a real server rather than a
patched function: what is under test includes the headers Control forwards
and how it reads the event stream.
"""

from __future__ import annotations

import base64
import io
import json
import socket
import threading
import time
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from PIL import Image


def picture(fmt: str = "PNG", size: tuple[int, int] = (8, 6), *, gps: bool = False) -> bytes:
    """A small image, optionally carrying a GPS position in its EXIF."""
    image = Image.new("RGB", size, (200, 40, 40))
    out = io.BytesIO()
    if gps:
        exif = Image.Exif()
        # GPSInfo: latitude 51 deg north, the kind of thing a phone writes.
        exif[0x8825] = {1: "N", 2: (51.0, 30.0, 0.0), 3: "W", 4: (0.0, 7.0, 0.0)}
        image.save(out, format=fmt, exif=exif)
    else:
        image.save(out, format=fmt)
    return out.getvalue()


class FakeChat:
    """Chat's `/images/stream`, answering as it is told to."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.headers: list[dict[str, str]] = []
        self.mode = "done"
        self.output = picture()
        self.seed = 1234
        app = FastAPI()

        @app.post("/api/v1/images/stream")
        async def stream(request: Request) -> Response:
            self.requests.append(await request.json())
            self.headers.append(dict(request.headers))
            if self.mode == "refuse":
                return JSONResponse(
                    {
                        "code": "validation_failed",
                        "title": "Not declared",
                        "detail": "That provider does not inpaint.",
                        "status": 422,
                    },
                    status_code=422,
                )
            return StreamingResponse(self.events(), media_type="text/event-stream")

        self.app = app

    async def events(self) -> Any:
        yield 'data: {"id": 0, "type": "progress", "value": 1, "max": 2}\n\n'
        if self.mode == "failed":
            failed = {
                "id": 1,
                "type": "failed",
                "code": "dependency_unavailable",
                "title": "Image provider timed out",
                "detail": "Try again.",
                "status": 504,
            }
            yield f"data: {json.dumps(failed)}\n\n"
            return
        last = self.requests[-1]
        result = {
            "provider_id": last["provider_id"],
            "model": last["model"],
            "images": [base64.b64encode(self.output).decode()] * last.get("count", 1),
            "seed": self.seed,
        }
        yield f"data: {json.dumps({'id': 1, 'type': 'done', 'result': result})}\n\n"


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class Running:
    """A FakeChat served on a free port until `stop`."""

    def __init__(self) -> None:
        self.fake = FakeChat()
        port = free_port()
        self.url = f"http://127.0.0.1:{port}"
        self._server = uvicorn.Server(
            uvicorn.Config(self.fake.app, port=port, log_level="error", lifespan="off")
        )
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + 10
        while not self._server.started:
            assert time.monotonic() < deadline, "the fake Chat did not start"
            time.sleep(0.01)

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=5)


def events_of(body: str) -> list[dict[str, Any]]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in body.splitlines()
        if line.startswith("data: ")
    ]
