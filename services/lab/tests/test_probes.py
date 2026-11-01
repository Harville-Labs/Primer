"""Linear probes: trained from two sets of prompts, and measured before they are trusted."""

from __future__ import annotations

import numpy as np
import pytest
from lab_owned import PROBE_POSITIVE as POSITIVE
from lab_owned import train
from lab_support import as_user
from primer_lab.probes import fit_probe, probability
from sklearn.linear_model import LogisticRegression


def synthetic(shuffled: bool) -> tuple[np.ndarray, np.ndarray]:
    """Two clouds a long way apart along one direction.

    Far more dimensions than rows, as a residual stream has against a few
    dozen prompts: that is where standardising each dimension drowns the one
    that carries the difference.
    """
    generator = np.random.default_rng(0)
    rows = generator.normal(size=(80, 1024))
    labels = np.array([1] * 40 + [0] * 40)
    rows[:, 0] += np.where(labels == 1, 6.0, -6.0)
    if shuffled:
        generator.shuffle(labels)
    return rows, labels


def test_a_probe_on_separable_activations_is_near_perfect_on_held_out_rows():
    fitted = fit_probe(*synthetic(shuffled=False))

    assert fitted.accuracy >= 0.95
    assert fitted.chance == 0.5
    assert fitted.beats_chance


def test_a_probe_on_shuffled_labels_is_near_chance_and_says_so():
    """It fits its training rows either way; only the held-out accuracy tells them apart."""
    rows, labels = synthetic(shuffled=True)
    fitted = fit_probe(rows, labels)

    training = np.mean((np.array(probability(fitted, rows)) > 0.5) == labels)
    assert training > 0.9
    assert abs(fitted.accuracy - 0.5) < 0.2
    assert not fitted.beats_chance


def test_a_probe_reads_rows_as_the_regression_it_was_fitted_as():
    """Kept as weights so it can be saved; it must still answer as scikit-learn would."""
    rows, labels = synthetic(shuffled=False)
    fitted = fit_probe(rows, labels)

    expected = LogisticRegression(max_iter=1000).fit(rows, labels).predict_proba(rows)[:, 1]
    assert probability(fitted, rows) == pytest.approx(expected.tolist(), abs=1e-9)


def test_chance_is_the_larger_set_s_share():
    rows, labels = synthetic(shuffled=False)
    fitted = fit_probe(rows[:60], labels[:60])

    assert fitted.chance == pytest.approx(40 / 60)


def test_folds_shrink_to_fit_the_smaller_set():
    rows, labels = synthetic(shuffled=False)
    keep = np.r_[0:40, 40:43]

    assert fit_probe(rows[keep], labels[keep]).folds == 3


def test_a_trained_probe_reports_how_it_was_measured(client):
    answer = train(client)

    assert answer.status_code == 201, answer.text
    probe = answer.json()
    assert probe["layer"] == 1
    assert (probe["positive"], probe["negative"]) == (4, 4)
    assert probe["folds"] == 4
    assert probe["chance"] == 0.5
    assert 0 <= probe["accuracy"] <= 1


@pytest.mark.parametrize(
    ("change", "why"),
    [
        ({"positive": POSITIVE[:3]}, "too few to hold any out"),
        ({"negative": ["w1"] * 65}, "too many"),
        ({"positive": [*POSITIVE[:3], "w1 " * 7000]}, "one too long"),
    ],
    ids=["three", "sixty-five", "long"],
)
def test_training_sets_are_bounded_in_count_and_length(client, change, why):
    assert train(client, **change).status_code == 422, why


def test_a_probe_at_a_layer_the_model_does_not_have_is_refused(client):
    answer = train(client, layer=2)

    assert answer.status_code == 422
    assert "0 to 1" in answer.json()["detail"]


def test_a_probe_is_listed_only_for_the_person_who_trained_it(shared):
    made = train(shared, headers=as_user("ada")).json()

    assert [p["id"] for p in shared.get("/api/v1/lab/probes", headers=as_user("ada")).json()] == [
        made["id"]
    ]
    assert shared.get("/api/v1/lab/probes", headers=as_user("bo")).json() == []
