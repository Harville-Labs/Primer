"""A stand-in for an operator's OpenAI-compatible vision endpoint.

A real HTTP server on a loopback port rather than a patched client, because
the thing in doubt is what Docling actually puts on the wire - the model
name, the key, the prompt - and a patch below that would assert nothing
about it. It records every request so a test can prove one was not made.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


@dataclass
class VisionEndpoint:
    base_url: str
    reply: str = "A bar chart of quarterly bookings rising to 4.2 million."
    status: int = 200
    requests: list[dict[str, Any]] = field(default_factory=list)


@contextmanager
def vision_endpoint(**kwargs: Any) -> Generator[VisionEndpoint]:
    endpoint = VisionEndpoint(base_url="", **kwargs)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            endpoint.requests.append(
                {"path": self.path, "headers": dict(self.headers), "body": body}
            )
            if endpoint.status != 200:
                self.send_response(endpoint.status)
                self.end_headers()
                return
            payload = json.dumps(
                {
                    "id": "fake",
                    "object": "chat.completion",
                    "created": 0,
                    "model": body.get("model"),
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": endpoint.reply},
                            "finish_reason": "stop",
                        }
                    ],
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    endpoint.base_url = f"http://127.0.0.1:{server.server_port}/v1"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield endpoint
    finally:
        server.shutdown()
        server.server_close()
