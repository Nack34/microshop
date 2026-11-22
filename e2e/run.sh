#!/usr/bin/env bash
# Starts the four services against a running RabbitMQ and runs the end-to-end scenario.
# Usage: RABBITMQ_URL=amqp://guest:guest@localhost:5672 ./e2e/run.sh
set -euo pipefail
cd "$(dirname "$0")/.."
export RABBITMQ_URL=${RABBITMQ_URL:-amqp://guest:guest@localhost:5672}
# Traces go to a small local OTLP receiver so the test can check them (Jaeger in docker compose).
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
# Stop the services started below when the script ends (each one is exec-ed, so its PID is the job PID).
trap 'kill $(jobs -p) 2>/dev/null; wait' EXIT

export LOG_DIR=${LOG_DIR:-$(mktemp -d)}
echo "service logs: $LOG_DIR"

# Refuse to run against leftovers from a previous run (they would answer instead of the fresh services).
python - <<'PY'
import socket, sys
busy = [p for p in (4318, 8001, 8002, 3001, 8080) if socket.socket().connect_ex(("127.0.0.1", p)) == 0]
sys.exit(f"ports already in use: {busy}" if busy else 0)
PY

python e2e/otlp_sink.py 4318 &
(cd catalog && exec python -m uvicorn app.main:app --port 8001 --log-level warning > "$LOG_DIR/catalog.log" 2>&1) &
(cd notifications && exec python -m uvicorn app.main:app --port 8002 --log-level warning > "$LOG_DIR/notifications.log" 2>&1) &
(cd orders && CATALOG_URL=http://localhost:8001 PORT=3001 exec node --no-warnings --import tsx src/index.ts > "$LOG_DIR/orders.log" 2>&1) &
(cd gateway && PORT=8080 exec node --no-warnings --import tsx src/index.ts > "$LOG_DIR/gateway.log" 2>&1) &

GATEWAY_URL=http://localhost:8080 python e2e/test_flow.py
