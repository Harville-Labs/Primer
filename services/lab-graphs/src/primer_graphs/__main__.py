"""Read a `GraphBuild` on stdin, write a `BuiltGraph` on stdout.

Lab runs this once per graph and kills it at its time limit. Anything that
goes wrong is written to stderr as one line and the exit status is 1; Lab
logs that line and tells the user the build failed, so it is the message
an operator reads.
"""

from __future__ import annotations

import logging
import os
import sys

# Before anything imports transformers or the hub client: the builder reads
# only files Lab already fetched and checked, and must never fetch its own.
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"


def main() -> int:
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    from primer_contracts.lab_graphs import GraphBuild
    from pydantic import ValidationError

    try:
        request = GraphBuild.model_validate_json(sys.stdin.buffer.read())
    except ValidationError as error:
        print(f"the build request is not valid: {error.errors()[0]['msg']}", file=sys.stderr)
        return 1

    from primer_graphs.build import build

    # Everything circuit-tracer raises ends here, so it is reported as the
    # build failing rather than as a traceback Lab would have to parse.
    try:
        graph = build(request)
    except Exception as error:  # noqa: BLE001
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 1
    sys.stdout.write(graph.model_dump_json())
    return 0


if __name__ == "__main__":
    sys.exit(main())
