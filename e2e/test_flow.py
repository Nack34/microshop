"""End-to-end scenario through the gateway: one order confirmed, one rejected for lack of stock.

Then checks the observability: metrics on every service, one trace spanning all four services
(HTTP + RabbitMQ hops), and logs carrying that trace's id.
"""

import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("GATEWAY_URL", "http://localhost:8080")


def call(method, path, body=None):
    req = urllib.request.Request(
        BASE + path,
        method=method,
        data=json.dumps(body).encode() if body else None,
        headers={"content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def wait_for(predicate, what, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            result = predicate()
            if result:
                return result
        except (urllib.error.URLError, ConnectionError):
            pass
        time.sleep(0.3)
    sys.exit(f"FAIL: timed out waiting for {what}")


def order_settled(order_id):
    status, order = call("GET", f"/api/orders/{order_id}")
    return order if status == 200 and order["status"] != "PENDING" else None


wait_for(lambda: call("GET", "/health")[0] == 200, "all services healthy")
print("✓ gateway reports all services up")

stock_before = call("GET", "/api/products/MN-27")[1]["stock"]

status, ok = call("POST", "/api/orders", {"customer": "ana@example.com", "items": [{"sku": "MN-27", "quantity": 2}]})
assert status == 202 and ok["status"] == "PENDING", (status, ok)
status, ko = call("POST", "/api/orders", {"customer": "beto@example.com", "items": [{"sku": "MN-27", "quantity": 1}]})
assert status == 202, (status, ko)
print("✓ two orders accepted as PENDING")

assert wait_for(lambda: order_settled(ok["id"]), "first order")["status"] == "CONFIRMED"
assert wait_for(lambda: order_settled(ko["id"]), "second order")["status"] == "REJECTED"
print("✓ saga settled: first CONFIRMED, second REJECTED (only 2 monitors in stock)")

assert call("GET", "/api/products/MN-27")[1]["stock"] == stock_before - 2
print("✓ catalog stock decreased exactly once")

ana = wait_for(lambda: call("GET", "/api/notifications?to=ana@example.com")[1], "ana's email")
beto = wait_for(lambda: call("GET", "/api/notifications?to=beto@example.com")[1], "beto's email")
assert ana[0]["subject"] == "Your order is confirmed" and "640,000" in ana[0]["body"]
assert "ran out of stock" in beto[0]["body"]
print("✓ notifications sent to both customers")
print("E2E PASSED")

# ---------------------------------------------------------------- observability
METRICS = {
    "gateway": "http://localhost:8080/metrics",
    "orders": "http://localhost:3001/metrics",
    "catalog": "http://localhost:8001/metrics/",
    "notifications": "http://localhost:8002/metrics/",
    "rabbitmq": os.environ.get("RABBITMQ_METRICS_URL", "http://localhost:15692/metrics/per-object"),
}
exported = set()
texts = {}
for service, url in METRICS.items():
    with urllib.request.urlopen(url, timeout=5) as r:
        texts[service] = r.read().decode()
    exported |= {line.split()[2] for line in texts[service].splitlines() if line.startswith("# TYPE ")}


def metric(service, series):
    """Value of one exact series on a service's /metrics page (0 if absent)."""
    for line in texts[service].splitlines():
        if line.startswith(series + " "):
            return float(line.split()[-1])
    return 0.0


assert metric("orders", 'shop_orders_settled_total{status="CONFIRMED"}') >= 1, texts["orders"][-1500:]
assert metric("orders", 'shop_orders_settled_total{status="REJECTED"}') >= 1
assert metric("catalog", 'shop_stock_reservations_total{outcome="rejected"}') >= 1
assert metric("catalog", 'shop_product_stock{sku="MN-27"}') == 0
posted = metric("gateway", 'http_request_duration_seconds_count{method="POST",route="/api/orders",status_code="202"}')
assert posted == 2, posted
print(f"✓ business and RED metrics exported ({len(exported)} metric families across 5 targets)")

names_file = pathlib.Path(os.environ.get("LOG_DIR", "/tmp")) / "metric-names.txt"
names_file.write_text("\n".join(sorted(exported)))
check = subprocess.run([sys.executable, "observability/check.py", "--metrics", str(names_file)], capture_output=True, text=True, check=False)
assert check.returncode == 0, check.stdout + check.stderr
print("✓ every alert and dashboard query references a metric that exists")


def full_trace():
    with urllib.request.urlopen(os.environ.get("OTLP_SINK_URL", "http://localhost:4318") + "/spans", timeout=5) as r:
        spans = json.loads(r.read())
    by_trace = {}
    for span in spans:
        by_trace.setdefault(span["trace_id"], []).append(span)
    for trace_id, group in by_trace.items():
        services = {s["service"] for s in group}
        # The POST that placed an order, followed over HTTP and both RabbitMQ round trips.
        if {"gateway", "orders", "catalog", "notifications"} <= services and any(
            s["name"] == "POST /api/orders" for s in group
        ):
            return trace_id, group
    return None


try:
    trace_id, trace_spans = wait_for(full_trace, "a trace that spans all four services", timeout=20)
except SystemExit:
    with urllib.request.urlopen(os.environ.get("OTLP_SINK_URL", "http://localhost:4318") + "/spans", timeout=5) as r:
        for span in json.loads(r.read()):
            print(f"  {span['trace_id'][:8]} {span['service']:<14} {span['name']}")
    raise
print(f"✓ one trace ({trace_id[:8]}…) follows the order across all 4 services, {len(trace_spans)} spans:")
for span in trace_spans:
    if not span["name"].startswith("middleware"):  # skip Express middleware noise in the printout
        print(f"    {span['service']:<14} {span['name']}")

log_dir = pathlib.Path(os.environ["LOG_DIR"]) if os.environ.get("LOG_DIR") else None
if log_dir:
    traced = [
        json.loads(line)
        for f in log_dir.glob("*.log")
        for line in f.read_text().splitlines()
        if line.startswith("{") and trace_id in line
    ]
    services = {entry["service"] for entry in traced}
    assert {"orders", "catalog", "notifications"} <= services, services
    print(f"✓ JSON logs from {', '.join(sorted(services))} carry the same trace_id")
print("OBSERVABILITY PASSED")
