"""Making the steering vectors and probes a test needs, as a caller would."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

VECTOR_POSITIVE = ["hello world", "w1 w2 w3"]
VECTOR_NEGATIVE = ["w9 w8", "w4 w5 w6 w7"]
# A probe holds some prompts out to score itself, so it needs more of them.
PROBE_POSITIVE = ["hello world", "w1 w2 w3", "w1 w3", "w2 w1 w3"]
PROBE_NEGATIVE = ["w9 w8", "w4 w5 w6 w7", "w5 w4", "w8 w9 w7"]


def make(client: TestClient, *, headers: dict[str, str] | None = None, **body: Any) -> Any:
    request = {"label": "test", "layer": 1, "positive": VECTOR_POSITIVE}
    request |= {"negative": VECTOR_NEGATIVE, "chat": False}
    return client.post("/api/v1/lab/vectors", json=request | body, headers=headers)


def train(client: TestClient, *, headers: dict[str, str] | None = None, **body: Any) -> Any:
    request = {"label": "test", "layer": 1, "positive": PROBE_POSITIVE}
    request |= {"negative": PROBE_NEGATIVE, "chat": False}
    return client.post("/api/v1/lab/probes", json=request | body, headers=headers)
