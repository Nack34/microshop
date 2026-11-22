"""Metrics, traces and JSON logs for this service.

- Metrics: Prometheus format on /metrics (request rate, errors, latency + business counters).
- Traces: OpenTelemetry, exported over OTLP/HTTP when OTEL_EXPORTER_OTLP_ENDPOINT is set.
  FastAPI and aio-pika are instrumented, so a trace follows an order through HTTP *and* RabbitMQ.
- Logs: one JSON object per line, carrying the trace_id so logs and traces can be joined.
"""

from __future__ import annotations

import json
import logging
import os
import time

from fastapi import FastAPI, Request
from opentelemetry import trace
from prometheus_client import Histogram, make_asgi_app

REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency",
    ["method", "route", "status_code"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
)


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        entry = {"level": record.levelname.lower(), "service": self.service, "msg": record.getMessage()}
        ctx = trace.get_current_span().get_span_context()
        if ctx.is_valid:
            entry["trace_id"] = format(ctx.trace_id, "032x")
        entry.update(getattr(record, "fields", {}))
        return json.dumps(entry)


def setup(app: FastAPI, service: str) -> logging.Logger:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter(service))
    log = logging.getLogger(service)
    log.handlers[:] = [handler]
    log.setLevel(logging.INFO)
    log.propagate = False

    @app.middleware("http")
    async def record_metrics(request: Request, call_next):
        started = time.perf_counter()
        response = await call_next(request)
        route = request.scope.get("route")
        # Label by route template (/products/{sku}), never the raw path, to keep cardinality bounded.
        path = getattr(route, "path", "unmatched")
        if path != "/metrics":
            REQUEST_DURATION.labels(request.method, path, str(response.status_code)).observe(
                time.perf_counter() - started
            )
        return response

    app.mount("/metrics", make_asgi_app())

    if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.instrumentation.aio_pika import AioPikaInstrumentor
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider(resource=Resource.create({"service.name": service}))
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(), schedule_delay_millis=500))
        trace.set_tracer_provider(provider)
        AioPikaInstrumentor().instrument()
        FastAPIInstrumentor.instrument_app(app, excluded_urls="health,metrics")
    return log
