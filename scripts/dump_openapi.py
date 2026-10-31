"""Write each service's OpenAPI schema to `schemas/`.

The schemas are checked in so that the web app's types can be generated
from them without a Python toolchain, and so that a contract change shows
up as a diff in a pull request rather than as a surprise at runtime. CI
regenerates them and fails on any difference.

The apps are built with authentication disabled and no database, because
nothing here is served: FastAPI assembles the schema from the routes and
the models, and neither depends on a connection.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from primer_chat.app import create_app as create_chat
from primer_chat.config import Settings as ChatSettings
from primer_control.app import create_app as create_control
from primer_control.config import Settings as ControlSettings
from primer_lab.app import create_app as create_lab
from primer_lab.config import Settings as LabSettings
from primer_retrieval.app import create_app as create_retrieval
from primer_retrieval.config import Settings as RetrievalSettings

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = REPO_ROOT / "schemas"


def schemas() -> dict[str, dict[str, Any]]:
    """One schema per service, by the name its file takes."""
    return {
        "control": create_control(ControlSettings(auth_mode="disabled")).openapi(),
        "chat": create_chat(ChatSettings(auth_mode="disabled")).openapi(),
        "retrieval": create_retrieval(RetrievalSettings()).openapi(),
        "lab": create_lab(LabSettings()).openapi(),
    }


def write() -> list[Path]:
    SCHEMAS.mkdir(exist_ok=True)
    written = []
    for name, schema in schemas().items():
        path = SCHEMAS / f"{name}.json"
        # Sorted keys and a trailing newline: the output has to be
        # byte-identical between runs, or the check that it has not drifted
        # would fail on the order a dict happened to come out in.
        path.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
        written.append(path)
    return written


if __name__ == "__main__":
    for path in write():
        print(path.relative_to(REPO_ROOT))
