"""The example Studio declarations are ones Chat would accept.

An operator copies these. One that Chat refuses at startup is worse than no
example, because the refusal names a field and not the file it came from.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from primer_contracts.images import ImageCapabilities

EXAMPLES = sorted((Path(__file__).resolve().parents[2] / "examples" / "studio").glob("*.json"))


def test_there_are_examples_to_check() -> None:
    assert EXAMPLES


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda path: path.name)
def test_each_example_is_a_valid_declaration(path: Path) -> None:
    ImageCapabilities.model_validate(json.loads(path.read_text()))
