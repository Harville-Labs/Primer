"""How Control reads its environment."""

from __future__ import annotations

import pytest
from primer_control.config import Settings


def test_an_empty_retention_variable_keeps_images_forever(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Compose passes an optional setting as "" rather than leaving it out."""
    monkeypatch.setenv("PRIMER_STUDIO_RETENTION_DAYS", "")

    assert Settings().studio_retention_days is None
