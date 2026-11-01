"""Feature labels: read from the operator's file, refused when they do not fit.

The SAE these tests label covers layer 1 only and is 64 features wide, so a
label at layer 0 or for feature 64 describes some other SAE.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from lab_support import SAES, build_saes, lab_with_saes, settle
from primer_lab.config import Settings
from primer_lab.labels import LabelsRefused, read_labels

PROMPT = "hello world w3 w7 w7"
COMMIT = "0" * 39 + "1"


def write_labels(weights_dir: Path, lines: list[Any], name: str = "labels.jsonl") -> Path:
    """A labels file in the weights volume, each entry one line; strings are written as is."""
    path = weights_dir / name
    path.write_text(
        "\n".join(line if isinstance(line, str) else json.dumps(line) for line in lines) + "\n"
    )
    return Path(name)


def every_feature(header: dict[str, Any] | None = None) -> list[Any]:
    """A label for each of the SAE's 64 features at layer 1."""
    return [header or {"sae": SAES}] + [
        {"layer": 1, "index": i, "label": f"feature number {i}"} for i in range(64)
    ]


def labelled_lab(weights_dir, hub, factory, tmp_path, relative: Path):
    hub.publish(SAES, build_saes(tmp_path / "saes", layers=[1]))
    return lab_with_saes(weights_dir, hub, factory, lab_feature_labels={SAES: relative})


def inspect(client) -> dict:
    answer = client.post(
        "/api/v1/lab/inspect", json={"prompt": PROMPT, "chat": False, "features": 4}
    )
    assert answer.status_code == 200, answer.text
    return answer.json()


def test_an_inspection_names_each_feature_that_fired_by_its_label(
    weights_dir, hub, factory, published, tmp_path
):
    with labelled_lab(
        weights_dir, hub, factory, tmp_path, write_labels(weights_dir, every_feature())
    ) as client:
        saes = settle(client)["model"]["saes"]
        layer = inspect(client)["layers"][1]

    assert saes["labelled"] == 64 and saes["labels_error"] is None, saes
    fired = {i for row in layer["feature_indices"] for i in row}
    assert fired
    assert layer["feature_labels"] == {str(i): f"feature number {i}" for i in sorted(fired)}


def test_a_feature_without_a_label_is_left_out_rather_than_named_blank(
    weights_dir, hub, factory, published, tmp_path
):
    relative = write_labels(
        weights_dir, [{"sae": SAES}, {"layer": 1, "index": 63, "label": "only this one"}]
    )
    with labelled_lab(weights_dir, hub, factory, tmp_path, relative) as client:
        settle(client)
        layer = inspect(client)["layers"][1]

    fired = {i for row in layer["feature_indices"] for i in row}
    assert set(map(int, layer["feature_labels"])) == fired & {63}


def test_without_a_labels_file_features_carry_no_labels(with_saes):
    layer = inspect(with_saes)["layers"][1]
    assert layer["feature_indices"] is not None
    assert layer["feature_labels"] is None
    assert with_saes.get("/api/v1/lab/status").json()["model"]["saes"]["labelled"] == 0


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"header": {"sae": SAES, "commit": "a" * 40}}, f"Lab fetched {COMMIT}"),
        ({"line": {"layer": 0, "index": 1, "label": "elsewhere"}}, "layer 0"),
        ({"line": {"layer": 1, "index": 64, "label": "too wide"}}, "feature 64"),
    ],
    ids=["another commit", "another layer", "another width"],
)
def test_labels_for_other_saes_are_refused_and_the_features_still_read(
    weights_dir, hub, factory, published, tmp_path, change, expected
):
    """A feature shown under another feature's name is worse than one shown by index."""
    lines = every_feature(change.get("header"))
    if "line" in change:
        lines.append(change["line"])
    with labelled_lab(
        weights_dir, hub, factory, tmp_path, write_labels(weights_dir, lines)
    ) as client:
        saes = settle(client)["model"]["saes"]
        layer = inspect(client)["layers"][1]

    assert saes["state"] == "ready"
    assert saes["labelled"] == 0
    assert expected in saes["labels_error"], saes["labels_error"]
    assert layer["feature_indices"] is not None
    assert layer["feature_labels"] is None


def refusal(weights_dir: Path, relative: Path) -> str:
    settings = Settings(lab_weights_dir=weights_dir, lab_feature_labels={SAES: relative})
    with pytest.raises(LabelsRefused) as refused:
        read_labels(settings)
    return str(refused.value)


@pytest.mark.parametrize(
    ("line", "problem"),
    [
        ("{not json", "not a JSON object"),
        ('{"layer": 1, "index": -1, "label": "negative"}', "index"),
        ('{"layer": 1, "index": 2}', "label"),
        ('{"layer": 1, "index": 2, "label": "%s"}' % ("x" * 121), "at most 120"),
        ('{"layer": 1, "index": 2, "label": "x", "colour": "red"}', "colour"),
        ('{"layer": 1, "index": 0, "label": "again"}', "labelled twice"),
    ],
    ids=["not json", "negative index", "no label", "label too long", "unknown field", "duplicate"],
)
def test_a_malformed_line_stops_startup_naming_the_file_and_line(
    weights_dir, hub, factory, published, tmp_path, line, problem
):
    relative = write_labels(
        weights_dir, [{"sae": SAES}, "", {"layer": 1, "index": 0, "label": "first"}, line]
    )
    hub.publish(SAES, build_saes(tmp_path / "saes", layers=[1]))

    with pytest.raises(LabelsRefused) as refused:
        labelled_lab(weights_dir, hub, factory, tmp_path, relative)

    message = str(refused.value)
    assert message.startswith("PRIMER_LAB_FEATURE_LABELS: labels.jsonl: line 4: "), message
    assert problem in message, message


def test_a_file_written_for_another_sae_repository_is_refused(weights_dir):
    relative = write_labels(weights_dir, every_feature({"sae": "someone/other-saes"}))
    message = refusal(weights_dir, relative)
    assert "line 1" in message and "someone/other-saes" in message and SAES in message


def test_a_file_that_is_not_text_is_refused_at_the_line_that_shows_it(weights_dir):
    (weights_dir / "labels.jsonl").write_bytes(b'{"sae": "lab/tiny-saes"}\n\xff\xfe\n')
    assert "line 2: it is not UTF-8 text" in refusal(weights_dir, Path("labels.jsonl"))


def test_an_empty_file_is_refused_saying_what_its_first_line_should_be(weights_dir):
    (weights_dir / "labels.jsonl").write_text("\n\n")
    assert '{"sae": "<repository>"}' in refusal(weights_dir, Path("labels.jsonl"))


@pytest.mark.parametrize("relative", ["../outside.jsonl", "/etc/passwd", "."])
def test_a_labels_file_outside_the_weights_volume_is_refused_unread(
    weights_dir, tmp_path, relative
):
    (tmp_path / "outside.jsonl").write_text(json.dumps({"sae": SAES}) + "\n")
    assert "not a file inside the weights volume" in refusal(weights_dir, Path(relative))


def test_a_symlink_out_of_the_weights_volume_is_refused(weights_dir, tmp_path):
    (tmp_path / "outside.jsonl").write_text(json.dumps({"sae": SAES}) + "\n")
    (weights_dir / "labels.jsonl").symlink_to(tmp_path / "outside.jsonl")
    assert "not a file inside the weights volume" in refusal(weights_dir, Path("labels.jsonl"))


def test_a_missing_labels_file_is_refused_naming_it(weights_dir):
    message = refusal(weights_dir, Path("nowhere.jsonl"))
    assert "nowhere.jsonl" in message and "no such file" in message


def test_an_operator_maps_labels_as_json(monkeypatch):
    monkeypatch.setenv("PRIMER_LAB_FEATURE_LABELS", json.dumps({SAES: "labels/tiny.jsonl"}))
    assert Settings().lab_feature_labels == {SAES: Path("labels/tiny.jsonl")}
