"""Notifications service: listens for order results and "sends" an email (kept in memory for the demo)."""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import aio_pika
from fastapi import FastAPI
from prometheus_client import Counter

from app import telemetry

EXCHANGE = "shop.events"
sent: list[dict] = []
seen: set[tuple[str, str]] = set()
SENT = Counter("shop_notifications_sent_total", "Emails sent", ["type"])
DUPLICATES = Counter("shop_notifications_duplicates_total", "Events ignored because they were already handled")


def render(key: str, event: dict) -> dict | None:
    """Turns an event into an email. Returns None for duplicates (events can be delivered twice)."""
    marker = (key, event["order_id"])
    if marker in seen:
        DUPLICATES.inc()
        return None
    seen.add(marker)
    if key == "order.confirmed":
        subject = "Your order is confirmed"
        body = f"Order {event['order_id'][:8]} is confirmed. Total: ${event['total']:,}."
    else:
        subject = "We couldn't confirm your order"
        body = f"Order {event['order_id'][:8]} was cancelled because a product ran out of stock. You weren't charged."
    return {"to": event["customer"], "subject": subject, "body": body, "at": datetime.now(UTC).isoformat()}


async def consume(url: str) -> None:
    connection = await aio_pika.connect_robust(url)
    channel = await connection.channel()
    exchange = await channel.declare_exchange(EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True)
    queue = await channel.declare_queue("notifications.order-results", durable=True)
    for key in ("order.confirmed", "order.rejected"):
        await queue.bind(exchange, key)
    async def on_message(message: aio_pika.abc.AbstractIncomingMessage) -> None:
        async with message.process(requeue=True):
            event = json.loads(message.body)
            email = render(message.routing_key, event)
            if email:
                sent.append(email)
                SENT.labels(message.routing_key.removeprefix("order.")).inc()
                log.info("email sent", extra={"fields": {"order_id": event["order_id"]}})

    # A consume callback (not queue.iterator) so the tracing instrumentation wraps the handler.
    await queue.consume(on_message)
    await asyncio.Future()


@asynccontextmanager
async def lifespan(_: FastAPI):
    task = asyncio.create_task(consume(os.environ["RABBITMQ_URL"])) if os.environ.get("RABBITMQ_URL") else None
    yield
    if task:
        task.cancel()


app = FastAPI(title="Notifications service", lifespan=lifespan)
log = telemetry.setup(app, "notifications")


@app.get("/health")
def health():
    return {"status": "ok", "service": "notifications"}


@app.get("/notifications")
def notifications(to: str | None = None):
    return [n for n in sent if to is None or n["to"] == to]
