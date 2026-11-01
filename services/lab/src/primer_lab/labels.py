"""Names for SAE features, from a file the operator supplies.

A feature is an index until someone has read what makes it fire and
written that down. Neuronpedia publishes such labels for many SAEs, but
asking it at runtime would send every feature a user looks at to a hosted
service, so Lab reads labels from a file in its weights volume instead and
calls nothing. The operator downloads or writes the file; Lab only reads it.

The file is JSON Lines. Its first line names the SAE repository it
describes, and optionally the commit; every line after it labels one
feature:

    {"sae": "Qwen/SAE-Res-Qwen3-8B-Base-W64K-L0_50", "commit": "..."}
    {"layer": 12, "index": 412, "label": "French words"}

The file is read whole at startup, and a line Lab cannot read stops it
starting: a deployment mistake reaches the operator rather than showing
users a half-labelled set. Whether the labels fit the SAEs is known only
once the SAEs are fetched, and a mismatch then leaves the features
unlabelled rather than the SAEs unusable.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from primer_lab.config import Settings
from primer_lab.sources import in_volume

#: Larger than any labels file for a 64K-wide SAE at every layer of a
#: 36-layer model, and small enough that a wrong path does not read a
#: model's weights into memory as text.
MAX_LABELS_BYTES = 256 * 1024 * 1024

ENV = "PRIMER_LAB_FEATURE_LABELS"

MAX_LABEL = 120


class LabelsRefused(ValueError):
    """A labels file Lab will not use, with a message naming what is wrong."""


class LabelsHeader(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sae: str = Field(min_length=1)
    commit: str | None = Field(default=None, min_length=1)


class LabelLine(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    layer: int = Field(ge=0)
    index: int = Field(ge=0)
    #: A name, not an explanation. Bounded because an inspection carries one
    #: per distinct feature that fired, which on a long prompt is thousands.
    label: str = Field(min_length=1, max_length=MAX_LABEL)


@dataclass(frozen=True, slots=True)
class FeatureLabels:
    """One SAE repository's labels, as read from the operator's file."""

    repository: str
    #: The commit they were written for, when the file says.
    commit: str | None
    labels: dict[tuple[int, int], str]

    def named(self, layer: int, indices: list[int]) -> dict[int, str]:
        """The labels for whichever of `indices` at `layer` have one."""
        return {i: self.labels[layer, i] for i in indices if (layer, i) in self.labels}

    def check(self, commit: str, width: int, layers: list[int]) -> None:
        """Refuse labels written for other SAEs than the ones fetched.

        Takes the SAEs' shape rather than the set itself: the set is built
        after this module is imported by the code that builds it.
        """
        if self.commit is not None and self.commit != commit:
            raise LabelsRefused(
                f"they are for commit {self.commit} of {self.repository}, and Lab fetched "
                f"{commit}. Labels for another commit may describe other features."
            )
        for layer, index in self.labels:
            if layer not in layers:
                raise LabelsRefused(f"they label layer {layer}, which these SAEs do not cover.")
            if index >= width:
                raise LabelsRefused(
                    f"they label feature {index} at layer {layer}, and these SAEs have {width}."
                )


def read_labels(settings: Settings) -> dict[str, FeatureLabels]:
    """Every labels file the operator mapped, by the SAE repository it labels."""
    return {
        repository: read_file(settings, repository, path)
        for repository, path in settings.lab_feature_labels.items()
    }


def read_file(settings: Settings, repository: str, relative: Path) -> FeatureLabels:
    def refuse(problem: str) -> LabelsRefused:
        return LabelsRefused(f"{ENV}: {relative}: {problem}")

    path = in_volume(settings, relative)
    if path is None:
        raise refuse("it is not a file inside the weights volume.")
    if not path.is_file():
        raise refuse("there is no such file in the weights volume.")
    if path.stat().st_size > MAX_LABELS_BYTES:
        raise refuse(f"it is larger than {MAX_LABELS_BYTES // (1024 * 1024)} MB.")

    header: LabelsHeader | None = None
    labels: dict[tuple[int, int], str] = {}
    # Read as bytes and decoded per line, so a file that is not text is
    # refused at the line that shows it rather than wherever iteration was.
    with path.open("rb") as lines:
        for number, raw in enumerate(lines, start=1):
            try:
                line = raw.decode("utf-8")
                if not line.strip():
                    continue
                if header is None:
                    header = LabelsHeader.model_validate_json(line)
                    if header.sae != repository:
                        raise refuse(
                            f"line {number}: it labels {header.sae}, and is mapped to {repository}."
                        )
                    continue
                entry = LabelLine.model_validate_json(line)
            except ValidationError as error:
                raise refuse(f"line {number}: {describe(error)}") from None
            except UnicodeDecodeError:
                raise refuse(f"line {number}: it is not UTF-8 text.") from None
            key = (entry.layer, entry.index)
            if key in labels:
                raise refuse(
                    f"line {number}: feature {entry.index} at layer {entry.layer} is labelled "
                    "twice."
                )
            labels[key] = entry.label
    if header is None:
        raise refuse('it is empty. Its first line should be {"sae": "<repository>"}.')
    return FeatureLabels(repository=repository, commit=header.commit, labels=labels)


def describe(error: ValidationError) -> str:
    problem = error.errors()[0]
    if problem["type"] == "json_invalid":
        return "it is not a JSON object."
    field = ".".join(map(str, problem["loc"]))
    return f"{field}: {problem['msg']}" if field else problem["msg"]
