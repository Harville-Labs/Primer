"""Scanning passages for injected instructions, a batch at a time.

The tiny model's weights are random, so what a probe finds here is only the
difference its training prompts made plain; that is enough to show the scan
reads each passage where the probe was trained to read.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from lab_support import as_user
from primer_lab.scan import MAX_SCAN_BATCH

#: What the injected passage and the probe's positive prompts end with.
INJECTED = "w40 w41 w42"
POSITIVE = [f"w1 w2 {INJECTED}", f"w5 w9 {INJECTED}", f"w3 {INJECTED}", f"w7 w6 w8 {INJECTED}"]
NEGATIVE = ["w1 w2 w3 w4", "w5 w9 w10 w11", "w3 w13 w14", "w7 w6 w8 w15"]


def scan(client: TestClient, passages: list[Any], **body: Any) -> Any:
    return client.post("/api/v1/lab/scan", json={"passages": passages} | body)


def passages(*texts: str) -> list[dict[str, str]]:
    return [{"id": f"p{index}", "text": text} for index, text in enumerate(texts)]


def test_a_seeded_injected_passage_ranks_first_on_the_probe(client):
    """The issue's acceptance case, on the test model."""
    trained = client.post(
        "/api/v1/lab/probes",
        json={
            "label": "injected",
            "layer": 1,
            "positive": POSITIVE,
            "negative": NEGATIVE,
            "chat": False,
        },
    )
    assert trained.status_code == 201, trained.text
    library = passages(
        "w2 w4 w6 w8 w10",
        "w11 w12 w13 w14",
        f"w20 w21 w22 {INJECTED}",
        "w30 w31 w32 w33 w34",
        "w9 w8 w7 w6",
    )

    answer = scan(client, library, probe_id=trained.json()["id"])

    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["probe"]["id"] == trained.json()["id"]
    assert [p["id"] for p in body["passages"]] == [p["id"] for p in library]
    ranked = sorted(body["passages"], key=lambda p: p["probe"], reverse=True)
    assert ranked[0]["id"] == "p2"
    assert ranked[0]["probe"] > 0.5 > ranked[1]["probe"]


def test_each_passage_is_scored_as_plain_text(client):
    """Mean and peak are over the same tokens `/score` reads in text mode."""
    text = "hello world w3 w7 w7 w12"
    scored = client.post("/api/v1/lab/score", json={"prompt": text, "mode": "text"}).json()
    bits = [token["surprisal_bits"] for token in scored["score"]["tokens"]]

    [passage] = scan(client, passages(text)).json()["passages"]

    assert passage["tokens"] == len(bits) == 5
    assert passage["mean_bits"] == pytest.approx(sum(bits) / len(bits), abs=1e-6)
    assert passage["peak_bits"] == pytest.approx(max(bits), abs=1e-6)
    assert passage["probe"] is None
    assert passage["truncated"] is False


def test_a_long_passage_is_read_from_its_start_rather_than_refused(client):
    """One long passage must not stop the rest of a library being scanned."""
    limit = client.app.state.settings.lab_max_prompt_tokens
    long, short = scan(client, passages("w1 " * (limit + 50), "w1 w2 w3")).json()["passages"]

    assert long["truncated"] is True
    assert long["tokens"] == limit - 1
    assert short["truncated"] is False


def test_a_one_token_passage_is_listed_unscored(client):
    [passage] = scan(client, passages("w1")).json()["passages"]

    assert (passage["tokens"], passage["mean_bits"], passage["probe"]) == (0, None, None)


@pytest.mark.parametrize(
    ("batch", "why"),
    [
        ([], "empty"),
        (passages(*["w1 w2"] * (MAX_SCAN_BATCH + 1)), "too many"),
        ([{"id": "p0", "text": "w1 " * 7000}], "one too long"),
        ([{"id": "x" * 65, "text": "w1 w2"}], "an id too long"),
    ],
)
def test_a_batch_is_bounded_in_count_and_length(client, batch, why):
    assert scan(client, batch).status_code == 422, why


def test_a_scan_names_no_library(client):
    """Lab is given text. A request naming a library is refused, not ignored."""
    answer = client.post(
        "/api/v1/lab/scan",
        json={"library_id": "00000000-0000-0000-0000-000000000001", "passages": passages("w1 w2")},
    )

    assert answer.status_code == 422


def test_someone_elses_probe_cannot_be_used_to_scan(shared):
    """A probe is computed from its maker's prompts, which may be private."""
    probe = shared.post(
        "/api/v1/lab/probes",
        json={"label": "x", "layer": 1, "positive": POSITIVE, "negative": NEGATIVE, "chat": False},
        headers=as_user("ada"),
    ).json()["id"]

    refused = shared.post(
        "/api/v1/lab/scan",
        json={"passages": passages("w1 w2"), "probe_id": probe},
        headers=as_user("bo"),
    )

    assert refused.status_code == 422
    assert "no probe with that id" in refused.json()["detail"]


def test_a_saved_scan_keeps_what_it_found_and_the_probe_it_used(saving):
    """Opened later, it reads without the library or a rerun; its probe comes back."""
    ada = as_user("ada")
    probe = saving.post(
        "/api/v1/lab/probes",
        json={"label": "x", "layer": 1, "positive": POSITIVE, "negative": NEGATIVE, "chat": False},
        headers=ada,
    ).json()
    scanned = saving.post(
        "/api/v1/lab/scan",
        json={"passages": passages(f"w20 {INJECTED}"), "probe_id": probe["id"]},
        headers=ada,
    ).json()
    passage = {
        "chunk_id": "00000000-0000-0000-0000-00000000000c",
        "document_id": "00000000-0000-0000-0000-00000000000d",
        "filename": "paper.pdf",
        "ordinal": 0,
        "content": f"w20 {INJECTED}",
        "locator": {"page": 3},
    }
    run = {
        "kind": "scan",
        "request": {
            "library_id": "00000000-0000-0000-0000-0000000000a1",
            "library_name": "Reading",
            "probe_id": probe["id"],
        },
        "result": {
            "model": scanned["model"],
            "probe": scanned["probe"],
            "scanned": 1,
            "total": 1,
            "findings": [{"passage": passage, "scored": scanned["passages"][0]}],
        },
    }

    saved = saving.post("/api/v1/lab/experiments", json={"title": "scan", "run": run}, headers=ada)

    assert saved.status_code == 201, saved.text
    opened = saving.get(f"/api/v1/lab/experiments/{saved.json()['id']}", headers=ada).json()
    assert opened["kind"] == "scan"
    assert opened["run"]["result"]["findings"][0]["passage"]["content"] == f"w20 {INJECTED}"
    assert [p["id"] for p in opened["probes"]] == [probe["id"]]
