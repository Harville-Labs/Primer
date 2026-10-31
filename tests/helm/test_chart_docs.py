"""The commands the operations docs give still select something.

A label selector that matches nothing is not an error to kubectl: `kubectl
scale -l ...` reports no resources and exits cleanly, so a stale selector
in a runbook fails silently at the moment someone is relying on it.
"""

from __future__ import annotations

import re
from pathlib import Path

from test_chart import render
from test_chart_graph import BUNDLED

DOCS = Path(__file__).resolve().parents[2] / "docs"

#: `component=name` and `component in (a,b)`, as kubectl spells them.
SELECTOR = re.compile(r"app\.kubernetes\.io/component(?:=([\w-]+)|\s+in\s+\(([^)]*)\))")


def documented_components() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for page in DOCS.rglob("*.md"):
        for match in SELECTOR.finditer(page.read_text()):
            names = match[1] or match[2]
            for name in names.split(","):
                found.setdefault(name.strip(), set()).add(str(page.relative_to(DOCS)))
    return found


def test_every_component_the_docs_select_is_one_the_chart_labels() -> None:
    documented = documented_components()
    assert documented, "no component selectors found; the pattern no longer matches the docs"
    rendered = {
        doc["metadata"].get("labels", {}).get("app.kubernetes.io/component")
        # The graph adds components of its own, which the docs also name.
        for doc in render(*BUNDLED)
    }
    missing = {name: pages for name, pages in documented.items() if name not in rendered}
    assert not missing, f"docs select components the chart never labels: {missing}"
