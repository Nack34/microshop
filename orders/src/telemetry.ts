/**
 * Tracing + structured logs. Import this module FIRST (before express/amqplib) so the
 * instrumentations can patch those libraries when they load.
 * Tracing is on only when OTEL_EXPORTER_OTLP_ENDPOINT is set (e.g. http://jaeger:4318).
 */
import { trace } from "@opentelemetry/api";
import { OTLPTraceExporter } from "@opentelemetry/exporter-trace-otlp-http";
import { NodeSDK } from "@opentelemetry/sdk-node";
import { HttpInstrumentation } from "@opentelemetry/instrumentation-http";
import { ExpressInstrumentation } from "@opentelemetry/instrumentation-express";
import { AmqplibInstrumentation } from "@opentelemetry/instrumentation-amqplib";
import { UndiciInstrumentation } from "@opentelemetry/instrumentation-undici";
export const SERVICE = "orders";

if (process.env.OTEL_EXPORTER_OTLP_ENDPOINT) {
  const sdk = new NodeSDK({
    serviceName: SERVICE,
    traceExporter: new OTLPTraceExporter(),
    instrumentations: [
      new HttpInstrumentation({ ignoreIncomingRequestHook: (req) => /^\/(health|metrics)/.test(req.url ?? "") }),
      new ExpressInstrumentation(),
      new AmqplibInstrumentation(),
      new UndiciInstrumentation(), // fetch() calls to the catalog
    ],
  });
  sdk.start();
  process.once("SIGTERM", () => void sdk.shutdown().finally(() => process.exit(0)));
}

/** One JSON object per line, with the active trace_id so logs can be joined to traces. */
export function log(msg: string, fields: Record<string, unknown> = {}) {
  if (process.env.NODE_ENV === "test") return;
  const ctx = trace.getActiveSpan()?.spanContext();
  console.log(JSON.stringify({ level: "info", service: SERVICE, msg, ...(ctx ? { trace_id: ctx.traceId } : {}), ...fields }));
}
