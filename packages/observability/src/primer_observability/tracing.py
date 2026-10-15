"""OpenTelemetry tracing, with the same content rule as logs.

Spans are attributed with ids and outcomes, never with text. A span
attribute is exactly as readable as a log line to whoever runs the collector,
and usually more widely retained.

W3C trace context propagates over HTTP and Celery, so one question's journey
through Chat, Retrieval, and the workers reads as a single trace without any
of them carrying what the question was.
"""

from __future__ import annotations

import logging
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger(__name__)

#: Attribute names Primer sets on spans. Ids and enumerations only.
SAFE_ATTRIBUTES = frozenset(
    {
        "primer.library_id",
        "primer.document_id",
        "primer.version_id",
        "primer.generation_id",
        "primer.job_id",
        "primer.conversation_id",
        "primer.stage",
        "primer.outcome",
        "primer.backend",
        "primer.chunk_count",
    }
)


def configure_tracing(service: str, endpoint: str | None = None) -> None:
    """Install a tracer provider, if an endpoint is configured.

    Without an endpoint this does nothing at all rather than exporting to a
    default. A telemetry pipeline that appeared because nobody configured
    one is exactly the surprise self-hosted software should not spring.
    """
    if not endpoint:
        logger.debug("no OTLP endpoint configured; tracing is off")
        return

    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = TracerProvider(resource=Resource.create({"service.name": service}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)


@contextmanager
def span_for(name: str, **attributes: Any) -> Generator[None]:
    """A span carrying only attributes from the safe set.

    Anything else is dropped rather than recorded, because a span attribute
    is as readable as a log line and usually kept for longer.
    """
    from opentelemetry import trace

    tracer = trace.get_tracer("primer")
    with tracer.start_as_current_span(name) as span:
        for key, value in attributes.items():
            if key in SAFE_ATTRIBUTES:
                span.set_attribute(key, value)
            else:
                logger.debug("dropped unsafe span attribute %s", key)
        yield
