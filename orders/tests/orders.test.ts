import request from "supertest";
import { describe, expect, it } from "vitest";
import { createApp, wireSaga } from "../src/app";
import { memoryBus } from "../src/bus";
import { OrderStore } from "../src/store";

const PRICES: Record<string, number> = { "KB-01": 82000, "MS-01": 15000 };

async function setup(prices = async (sku: string) => PRICES[sku] ?? null) {
  const store = new OrderStore();
  const bus = memoryBus();
  await wireSaga(store, bus);
  return { store, bus, app: createApp(store, bus, prices) };
}

describe("orders service", () => {
  it("creates a pending order with catalog prices and publishes order.created", async () => {
    const { app, bus } = await setup();
    const res = await request(app)
      .post("/orders")
      .send({ customer: "ana@example.com", items: [{ sku: "KB-01", quantity: 2 }, { sku: "MS-01", quantity: 1 }] });
    expect(res.status).toBe(202);
    expect(res.body.status).toBe("PENDING");
    expect(res.body.total).toBe(179000);
    expect(bus.published[0]).toEqual({
      key: "order.created",
      body: { order_id: res.body.id, items: [{ sku: "KB-01", quantity: 2 }, { sku: "MS-01", quantity: 1 }] },
    });
  });

  it("confirms or rejects when the catalog answers, and publishes the result once", async () => {
    const { app, bus, store } = await setup();
    const ok = (await request(app).post("/orders").send({ customer: "ana@example.com", items: [{ sku: "KB-01", quantity: 1 }] })).body;
    const ko = (await request(app).post("/orders").send({ customer: "beto@example.com", items: [{ sku: "MS-01", quantity: 1 }] })).body;

    await bus.publish("stock.reserved", { order_id: ok.id });
    await bus.publish("stock.rejected", { order_id: ko.id });
    await bus.publish("stock.reserved", { order_id: ok.id }); // redelivered event

    expect(store.get(ok.id)?.status).toBe("CONFIRMED");
    expect(store.get(ko.id)?.status).toBe("REJECTED");
    const results = bus.published.filter((p) => p.key.startsWith("order.") && p.key !== "order.created");
    expect(results.map((r) => r.key)).toEqual(["order.confirmed", "order.rejected"]);
    expect(results[0].body).toEqual({ order_id: ok.id, customer: "ana@example.com", total: 82000 });
  });

  it("validates input and unknown products", async () => {
    const { app } = await setup();
    expect((await request(app).post("/orders").send({ customer: "nope", items: [] })).status).toBe(400);
    const unknown = await request(app).post("/orders").send({ customer: "a@b.com", items: [{ sku: "ZZ", quantity: 1 }] });
    expect(unknown.status).toBe(400);
    expect(unknown.body.error).toContain("ZZ");
  });

  it("answers 503 when the catalog is down", async () => {
    const { app, bus } = await setup(async () => {
      throw new Error("timeout");
    });
    const res = await request(app).post("/orders").send({ customer: "a@b.com", items: [{ sku: "KB-01", quantity: 1 }] });
    expect(res.status).toBe(503);
    expect(bus.published).toEqual([]);
  });

  it("returns 404 for unknown orders", async () => {
    const { app } = await setup();
    expect((await request(app).get("/orders/nope")).status).toBe(404);
  });
});

describe("metrics", () => {
  it("exposes request latency by route template and saga counters", async () => {
    const { app, bus } = await setup();
    const order = (await request(app).post("/orders").send({ customer: "m@x.com", items: [{ sku: "KB-01", quantity: 1 }] })).body;
    await request(app).get(`/orders/${order.id}`);
    await bus.publish("stock.reserved", { order_id: order.id });
    const text = (await request(app).get("/metrics")).text;
    expect(text).toContain('http_request_duration_seconds_count{method="GET",route="/orders/:id",status_code="200"}');
    expect(text).not.toContain(order.id); // raw ids never become labels
    expect(text).toMatch(/shop_orders_created_total \d+/);
    expect(text).toMatch(/shop_orders_settled_total\{status="CONFIRMED"\} \d+/);
    expect(text).toContain("shop_order_saga_duration_seconds_bucket");
  });
});
