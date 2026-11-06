"""Generate Primer's architecture diagrams.

Run: uv run python scripts/generate_diagrams.py

Diagrams are generated rather than drawn so they stay reviewable in a diff
and cannot quietly drift from the system they describe: changing one is a
code change, with the reason in the commit message.
"""

from __future__ import annotations

from itertools import pairwise
from pathlib import Path

from svgkit import Box, Canvas

OUTPUT = Path(__file__).resolve().parents[1] / "docs" / "assets" / "diagrams"


def system_architecture() -> Canvas:
    """Which process owns what, and what is allowed to talk to what."""
    canvas = Canvas(1300, 760, "Primer system architecture")

    browser = canvas.box(Box(60, 70, 210, 62, "Browser", "the researcher", "client"))
    proxy = canvas.box(Box(60, 196, 210, 76, "oauth2-proxy", "verifies with the IdP", "edge"))
    web = canvas.box(Box(60, 336, 210, 62, "Web app", "SvelteKit", "client"))

    lab = canvas.box(Box(420, 96, 250, 80, "Lab", "optional, loads its own model", "service"))
    control = canvas.box(
        Box(420, 230, 250, 88, "Control API", "libraries, documents, jobs", "service")
    )
    broker = canvas.box(Box(420, 370, 250, 62, "RabbitMQ", "one queue per stage", "store"))
    workers = canvas.box(
        Box(420, 490, 250, 80, "Ingestion workers", "parse, embed, index", "worker")
    )
    extraction = canvas.box(
        Box(420, 640, 250, 62, "Graph extraction", "OpenAI-compatible, optional", "external")
    )

    chat = canvas.box(Box(780, 96, 250, 80, "Chat", "answers, tools, images", "service"))
    postgres = canvas.box(Box(780, 240, 250, 66, "PostgreSQL", "a schema per service", "store"))
    sources = canvas.box(Box(780, 370, 250, 66, "Source objects", "fsspec: disk or S3", "store"))
    retrieval = canvas.box(Box(780, 490, 250, 88, "Retrieval", "the only index client", "service"))
    vectors = canvas.box(Box(780, 640, 250, 62, "Vector store", "Qdrant", "store"))

    models = canvas.box(Box(1100, 96, 170, 80, "Your endpoints", "chat, MCP, images", "external"))
    embeddings = canvas.box(Box(1100, 490, 170, 88, "Embeddings", "embedder, reranker", "external"))
    graph = canvas.box(Box(1100, 640, 170, 62, "Knowledge graph", "Neo4j, always on", "store"))

    canvas.group(30, 36, 270, 396, "Outside the cluster")
    canvas.group(370, 30, 915, 690, "Inside the cluster")

    canvas.arrow(browser.port("bottom"), proxy.port("top"))
    canvas.arrow(proxy.port("bottom"), web.port("top"), label="signed in")

    # The web app is the only caller of Control, Chat, and Lab, and forwards
    # the proxy's identity headers to each. One trunk up the gap between the
    # two groups, rather than three lines crossing the cluster's edge.
    trunk = 335
    canvas.route(
        [
            (web.x + web.w, web.cy),
            (trunk, web.cy),
            (trunk, control.y + 20),
            (control.x, control.y + 20),
        ]
    )
    canvas.route([(trunk, control.y + 20), (trunk, lab.cy), (lab.x, lab.cy)])
    # Labelled along the top, where the chip has room; on the trunk itself
    # it would sit across both groups' edges.
    canvas.route(
        [(trunk, lab.cy), (trunk, 66), (chat.cx, 66), (chat.cx, chat.y)],
        label="identity headers",
        label_at=2,
    )

    # Chat asks Control who may read a library before it searches one.
    canvas.route(
        [
            (chat.x, chat.y + 70),
            (725, chat.y + 70),
            (725, control.y + 16),
            (control.x + control.w, control.y + 16),
        ]
    )
    canvas.arrow(chat.port("right"), models.port("left"))
    canvas.route(
        [
            (chat.x + chat.w, chat.y + 70),
            (1060, chat.y + 70),
            (1060, retrieval.y + 14),
            (retrieval.x + retrieval.w, retrieval.y + 14),
        ],
        label="scoped search",
        label_at=2,
    )

    canvas.arrow(control.port("bottom"), broker.port("top"), label="job id")
    canvas.arrow(broker.port("bottom"), workers.port("top"))
    canvas.arrow(
        (control.x + control.w, postgres.cy), (postgres.x, postgres.cy), label="reads, writes"
    )
    canvas.arrow(workers.port("right"), retrieval.port("left"), label="chunks, graphs")
    canvas.arrow(workers.port("bottom"), extraction.port("top"), dashed=True)
    canvas.arrow(retrieval.port("bottom"), vectors.port("top"), label="scoped filter")
    canvas.arrow(retrieval.port("right"), embeddings.port("left"), dashed=True)
    # Below the embeddings arrow and right of the vector store, so neither
    # line crosses a box.
    canvas.route(
        [
            (retrieval.x + retrieval.w, retrieval.y + 76),
            (1065, retrieval.y + 76),
            (1065, graph.cy),
            (graph.x, graph.cy),
        ]
    )

    # Control writes uploads; workers read them back. Routed around the
    # Control box rather than through it.
    canvas.route(
        [
            (control.x + 200, control.y + control.h),
            (control.x + 200, 344),
            (sources.x, 344),
            (sources.x, sources.cy),
        ],
        label="writes uploads",
        label_at=2,
    )
    canvas.route(
        [
            (workers.x + 50, workers.y),
            (workers.x + 50, 460),
            (sources.cx, 460),
            (sources.cx, sources.y + sources.h),
        ],
        label="reads bytes",
        dashed=True,
        label_at=2,
    )

    # Job transitions return to Control down a gutter inside the cluster's
    # edge, rather than crossing back through the column they came from. It
    # meets Control below the web app's trunk so the two never cross.
    canvas.route(
        [
            (workers.x, workers.cy + 16),
            (395, workers.cy + 16),
            (395, control.cy + 26),
            (control.x, control.cy + 26),
        ],
        label="claim, complete, fail",
        label_at=2,
        label_dy=-90,
    )

    canvas.label(
        650,
        736,
        "Workers hold no database credentials: every job transition is a Control request.",
        size=12,
    )
    return canvas


def ingestion_pipeline() -> Canvas:
    """The states a document walks, and the ones it can stop at."""
    canvas = Canvas(1240, 620, "Ingestion pipeline states")

    row, width, height, gap = 190, 158, 64, 38
    names = [
        ("queued", "upload committed"),
        ("parsing", "Docling converts"),
        ("chunking", "awaiting embed"),
        ("embedding", "vectors and graph"),
        ("indexing", "verifying counts"),
        ("ready", "searchable"),
    ]
    boxes = [
        canvas.box(
            Box(
                46 + index * (width + gap),
                row,
                width,
                height,
                name,
                note,
                "terminal" if name == "ready" else "state",
                mono=True,
            )
        )
        for index, (name, note) in enumerate(names)
    ]

    for label, index in (("parse", 0), ("embed", 2), ("index", 4)):
        left, right = boxes[index], boxes[index + 1]
        canvas.add(
            f'<path d="M {left.x} {row - 40} L {left.x} {row - 26} '
            f"M {left.x} {row - 40} L {right.x + width} {row - 40} "
            f'M {right.x + width} {row - 40} L {right.x + width} {row - 26}" '
            f'fill="none" class="edge" stroke-width="1.2"/>'
        )
        canvas.label(
            (left.x + right.x + width) / 2,
            row - 56,
            f"stage: {label}",
            size=12,
            weight=700,
            muted=False,
        )

    for left, right in pairwise(boxes):
        canvas.arrow(left.port("right"), right.port("left"))

    failed = canvas.box(Box(150, 420, 180, 64, "failed", "retries exhausted", "failure", mono=True))
    unsupported = canvas.box(
        Box(420, 420, 200, 64, "unsupported", "nothing readable", "failure", mono=True)
    )
    cancelled = canvas.box(Box(710, 420, 180, 64, "cancelled", "superseded", "failure", mono=True))

    canvas.route(
        [
            (boxes[1].cx - 20, boxes[1].y + height),
            (boxes[1].cx - 20, 344),
            (failed.cx, 344),
            (failed.cx, failed.y),
        ],
        label="budget spent",
        label_at=2,
    )
    canvas.route(
        [
            (boxes[1].cx + 34, boxes[1].y + height),
            (boxes[1].cx + 34, 386),
            (unsupported.cx, 390),
            (unsupported.cx, unsupported.y),
        ]
    )
    canvas.route(
        [
            (boxes[3].cx, boxes[3].y + height),
            (boxes[3].cx, 366),
            (cancelled.cx, 366),
            (cancelled.cx, cancelled.y),
        ],
        dashed=True,
    )

    # A retryable failure releases the lease and returns the job to its
    # stage's entry state. Routed below the row, clear of the stage brackets.
    canvas.route(
        [
            (boxes[1].x + 24, boxes[1].y + height),
            (boxes[1].x + 24, 292),
            (boxes[0].cx, 292),
            (boxes[0].cx, boxes[0].y + height),
        ],
        label="retry",
        dashed=True,
        label_at=2,
    )

    canvas.label(
        620,
        540,
        "A redelivered message finds the job past its stage's entry state and does nothing.",
        size=12,
    )
    canvas.label(
        620,
        566,
        "A worker that dies stops renewing its lease, and the stage becomes claimable again.",
        size=12,
    )
    return canvas


def authorization_path() -> Canvas:
    """Where identity comes from, and where access is decided."""
    canvas = Canvas(1180, 560, "Identity and authorization")

    request = canvas.box(Box(56, 76, 220, 68, "Request", "no identity of its own", "client"))
    proxy = canvas.box(Box(56, 208, 220, 82, "oauth2-proxy", "verifies with the IdP", "edge"))
    headers = canvas.box(
        Box(56, 356, 220, 82, "X-Forwarded-User", "trusted from the edge only", "edge", mono=True)
    )

    principal = canvas.box(
        Box(420, 208, 250, 82, "Principal", "user id from the subject", "service")
    )
    access = canvas.box(
        Box(420, 356, 250, 82, "LibraryAccess", "returns a SQL predicate", "service")
    )
    query = canvas.box(Box(790, 356, 300, 82, "Every query", "filtered, not post-checked", "store"))
    answer = canvas.box(
        Box(790, 208, 300, 82, "404, never 403", "absence and denial look alike", "terminal")
    )

    canvas.group(28, 44, 272, 430, "Outside")
    canvas.group(388, 172, 730, 302, "Control API")

    canvas.arrow(request.port("bottom"), proxy.port("top"))
    canvas.arrow(proxy.port("bottom"), headers.port("top"))
    canvas.arrow(
        proxy.port("right"), principal.port("left"), label="subject, email, groups", label_dy=-30
    )
    canvas.arrow(headers.port("right"), access.port("left"))
    canvas.arrow(principal.port("bottom"), access.port("top"))
    canvas.arrow(access.port("right"), query.port("left"))
    canvas.arrow(query.port("top"), answer.port("bottom"), label="no rows")

    canvas.label(
        573,
        512,
        "Primer never validates tokens. With auth off, the edge is replaced by one "
        "fixed local user.",
        size=12,
    )
    return canvas


def generation_lifecycle() -> Canvas:
    """How a rebuild becomes visible without a moment of partial truth."""
    canvas = Canvas(1180, 470, "Index generations")

    phases = [
        (60, "Before", [("Generation A", "answering searches", "terminal")]),
        (
            450,
            "During the rebuild",
            [
                ("Generation A", "still answering", "terminal"),
                ("Generation B", "written, unsearchable", "state"),
            ],
        ),
        (
            840,
            "After activation",
            [
                ("Generation B", "answering searches", "terminal"),
                ("Generation A", "retired, then deleted", "store"),
            ],
        ),
    ]
    for x, title, boxes in phases:
        canvas.group(x, 92, 290, 216, title)
        for index, (name, note, kind) in enumerate(boxes):
            canvas.box(Box(x + 25, 130 + index * 86, 240, 68, name, note, kind))
        if len(boxes) == 1:
            canvas.label(x + 145, 250, "no rebuild in flight", size=12)

    canvas.arrow((350, 200), (450, 200))
    canvas.label(400, 176, "index chunks", size=12, muted=False)
    canvas.arrow((740, 200), (840, 200))
    canvas.label(790, 176, "counts verified", size=12, muted=False)

    canvas.label(
        590,
        372,
        "Searches read only the active generation, so a half-written rebuild is invisible.",
        size=12,
    )
    canvas.label(
        590,
        398,
        "Activation is one row update in Control: every answer changes at the same instant.",
        size=12,
    )
    canvas.label(
        590,
        424,
        "A short or failed build never activates, so nothing silently loses passages.",
        size=12,
    )
    return canvas


DIAGRAMS = {
    "architecture": system_architecture,
    "ingestion-pipeline": ingestion_pipeline,
    "authorization": authorization_path,
    "generations": generation_lifecycle,
}


def main() -> None:
    for name, build in DIAGRAMS.items():
        for path in build().write(OUTPUT / f"{name}.svg"):
            print(f"wrote {path}")


if __name__ == "__main__":
    main()
