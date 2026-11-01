"""Reading Lab's generation stream in a test."""

from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient

PROMPT = "hello world w3 w7 w7"


def events(text: str) -> list[dict[str, Any]]:
    """The events of a server-sent stream, each its JSON body with its `id:` checked."""
    parsed = []
    for frame in filter(None, text.split("\n\n")):
        fields = dict(line.split(": ", 1) for line in frame.splitlines())
        body = json.loads(fields["data"])
        assert int(fields["id"]) == body["id"]
        assert fields["event"] == body["type"]
        parsed.append(body)
    return parsed


def generate(client: TestClient, *, headers: dict[str, str] | None = None, **body: Any) -> Any:
    request = {"prompt": PROMPT, "chat": False, "max_new_tokens": 6} | body
    return client.post("/api/v1/lab/generate", json=request, headers=headers)


def completed(client: TestClient, **body: Any) -> dict[str, Any]:
    """The `completed` event of a generation that is expected to succeed."""
    answer = generate(client, **body)
    assert answer.status_code == 200, answer.text
    last = events(answer.text)[-1]
    assert last["type"] == "completed", last
    return last
