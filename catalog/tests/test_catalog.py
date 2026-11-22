import asyncio

from app import main
from app.store import CatalogStore
from fastapi.testclient import TestClient


def test_reserve_is_all_or_nothing():
    store = CatalogStore()
    assert store.reserve("o1", [{"sku": "KB-01", "quantity": 2}, {"sku": "MN-27", "quantity": 5}]) == "rejected"
    assert store.get("KB-01").stock == 10  # rolled back
    assert store.reserve("o2", [{"sku": "KB-01", "quantity": 2}, {"sku": "MN-27", "quantity": 2}]) == "reserved"
    assert store.get("KB-01").stock == 8 and store.get("MN-27").stock == 0


def test_reserve_is_idempotent_for_redelivered_events():
    store = CatalogStore()
    assert store.reserve("o1", [{"sku": "MS-01", "quantity": 5}]) == "reserved"
    assert store.reserve("o1", [{"sku": "MS-01", "quantity": 5}]) == "reserved"
    assert store.get("MS-01").stock == 20  # reserved once


def test_unknown_sku_is_rejected():
    assert CatalogStore().reserve("o1", [{"sku": "NOPE", "quantity": 1}]) == "rejected"


def test_handler_publishes_the_outcome():
    main.store = CatalogStore()
    sent = []

    async def publish(key, body):
        sent.append((key, body))

    asyncio.run(main.handle_order_created({"order_id": "o9", "items": [{"sku": "MN-27", "quantity": 3}]}, publish))
    assert sent == [("stock.rejected", {"order_id": "o9", "outcome": "rejected"})]


def test_http_api():
    main.store = CatalogStore()
    client = TestClient(main.app)
    assert [p["sku"] for p in client.get("/products").json()] == ["KB-01", "MN-27", "MS-01"]
    assert client.get("/products/KB-01").json()["price"] == 82000
    assert client.get("/products/X").status_code == 404


def test_metrics_endpoint_exposes_red_and_business_metrics():
    main.store = CatalogStore()
    client = TestClient(main.app)
    client.get("/products/KB-01")
    client.get("/products/NOPE")
    asyncio.run(main.handle_order_created({"order_id": "m1", "items": [{"sku": "MS-01", "quantity": 1}]}, _noop))
    text = client.get("/metrics/").text
    assert 'http_request_duration_seconds_count{method="GET",route="/products/{sku}",status_code="404"}' in text
    assert 'shop_stock_reservations_total{outcome="reserved"}' in text
    assert 'shop_product_stock{sku="MS-01"} 24.0' in text


async def _noop(*_):
    pass
