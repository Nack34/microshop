"""A tiny OTLP/HTTP trace receiver for the end-to-end test.

Accepts both encodings (Node exports JSON, Python exports protobuf) and keeps a flat list of spans,
served back on GET /spans. In production the services export to Jaeger instead.
"""

import gzip
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from google.protobuf.json_format import MessageToDict
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)

spans: list[dict] = []


def collect(payload: dict) -> None:
    for resource_spans in payload.get("resourceSpans", []):
        attrs = {a["key"]: a["value"].get("stringValue") for a in resource_spans.get("resource", {}).get("attributes", [])}
        for scope in resource_spans.get("scopeSpans", []):
            for span in scope.get("spans", []):
                spans.append(
                    {
                        "service": attrs.get("service.name"),
                        "trace_id": span["traceId"],
                        "name": span["name"],
                        "kind": span.get("kind"),
                    }
                )


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _read_body(self) -> bytes:
        if self.headers.get("transfer-encoding", "").lower() != "chunked":
            return self.rfile.read(int(self.headers.get("content-length", 0)))
        chunks = []
        while True:
            size = int(self.rfile.readline().split(b";")[0].strip(), 16)
            if size == 0:
                self.rfile.readline()  # blank line after the last chunk
                return b"".join(chunks)
            chunks.append(self.rfile.read(size))
            self.rfile.readline()  # CRLF after each chunk

    def do_POST(self):
        body = self._read_body()
        if self.path != "/v1/traces":  # metrics/logs exports are not needed by the test
            self.send_response(200)
            self.send_header("content-length", "0")
            self.end_headers()
            return
        if self.headers.get("content-encoding") == "gzip":
            body = gzip.decompress(body)
        if "json" in self.headers.get("content-type", ""):
            collect(json.loads(body))
        else:
            request = ExportTraceServiceRequest.FromString(body)
            # Protobuf carries ids as bytes; normalise to hex like the JSON encoding.
            payload = MessageToDict(request)
            for rs, raw in zip(payload.get("resourceSpans", []), request.resource_spans, strict=True):
                for scope, raw_scope in zip(rs.get("scopeSpans", []), raw.scope_spans, strict=True):
                    for span, raw_span in zip(scope.get("spans", []), raw_scope.spans, strict=True):
                        span["traceId"] = raw_span.trace_id.hex()
            collect(payload)
        self.send_response(200)
        self.send_header("content-length", "0")
        self.end_headers()

    def do_GET(self):
        data = json.dumps(spans).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1]) if len(sys.argv) > 1 else 4318), Handler).serve_forever()
