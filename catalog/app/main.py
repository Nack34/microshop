"""Catalog service: product API + a consumer that reserves stock when an order is created."""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager

import aio_pika
from fastapi import FastAPI, HTTPException
from prometheus_client import Counter, Gauge

from app import telemetry
from app.store import CatalogStore

EXCHANGE = "shop.events"
store = CatalogStore(os.environ.get("CATALOG_DB", ":memory:"))

RESERVATIONS = Counter("shop_stock_reservations_total", "Stock reservation attempts", ["outcome"])
STOCK = Gauge("shop_product_stock", "Units in stock", ["sku"])


def refresh_stock_gauge() -> None:
    for product in store.products():
        STOCK.labels(product.sku).set(product.stock)


async def handle_order_created(event: dict, publish) -> None:
    outcome = store.reserve(event["order_id"], event["items"])
    RESERVATIONS.labels(outcome).inc()
    refresh_stock_gauge()
    log.info("stock %s", outcome, extra={"fields": {"order_id": event["order_id"]}})
    routing_key = "stock.reserved" if outcome == "reserved" else "stock.rejected"
    await publish(routing_key, {"order_id": event["order_id"], "outcome": outcome})


async def consume(url: str) -> None:
    connection = await aio_pika.connect_robust(url)
    channel = await connection.channel()
    await channel.set_qos(prefetch_count=10)
    exchange = await channel.declare_exchange(EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True)
    queue = await channel.declare_queue("catalog.order-created", durable=True)
    await queue.bind(exchange, "order.created")

    async def publish(key: str, body: dict) -> None:
        await exchange.publish(
            aio_pika.Message(json.dumps(body).encode(), delivery_mode=aio_pika.DeliveryMode.PERSISTENT), key
        )

    async def on_message(message: aio_pika.abc.AbstractIncomingMessage) -> None:
        # Ack only after handling: if the service crashes mid-way, RabbitMQ redelivers the event.
        async with message.process(requeue=True):
            await handle_order_created(json.loads(message.body), publish)

    # A consume callback (not queue.iterator) so the tracing instrumentation wraps the handler
    # and the reply we publish stays in the same trace as the incoming event.
    await queue.consume(on_message)
    await asyncio.Future()  # keep consuming until the task is cancelled


@asynccontextmanager
async def lifespan(_: FastAPI):
    task = None
    if url := os.environ.get("RABBITMQ_URL"):
        task = asyncio.create_task(consume(url))
    yield
    if task:
        task.cancel()


app = FastAPI(title="Catalog service", lifespan=lifespan)
log = telemetry.setup(app, "catalog")
refresh_stock_gauge()


@app.get("/health")
def health():
    return {"status": "ok", "service": "catalog"}


@app.get("/products")
def products():
    return store.products()


@app.get("/products/{sku}")
def product(sku: str):
    found = store.get(sku)
    if not found:
        raise HTTPException(404, "Product not found")
    return found
