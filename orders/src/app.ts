import express from "express";
import { z } from "zod";
import type { Bus } from "./bus";
import { httpMetrics, metricsHandler, ordersCreated, ordersSettled, sagaDuration } from "./metrics";
import type { OrderStore } from "./store";
import { log } from "./telemetry";

const orderSchema = z.object({
  customer: z.string().email(),
  items: z
    .array(z.object({ sku: z.string().min(1).max(20), quantity: z.number().int().min(1).max(50) }))
    .min(1)
    .max(20),
});

export type PriceLookup = (sku: string) => Promise<number | null>;

/** Asks the catalog for current prices (a synchronous call with a timeout). */
export function catalogPrices(baseUrl: string, timeoutMs = 2000): PriceLookup {
  return async (sku) => {
    const res = await fetch(`${baseUrl}/products/${encodeURIComponent(sku)}`, { signal: AbortSignal.timeout(timeoutMs) });
    if (res.status === 404) return null;
    if (!res.ok) throw new Error(`catalog answered ${res.status}`);
    return (await res.json()).price;
  };
}

export function createApp(store: OrderStore, bus: Bus, prices: PriceLookup) {
  const app = express();
  app.use(express.json({ limit: "20kb" }));
  app.use(httpMetrics((req) => (req.route ? req.baseUrl + req.route.path : "unmatched")));
  app.get("/metrics", metricsHandler);

  app.get("/health", (_req, res) => {
    res.json({ status: "ok", service: "orders" });
  });

  app.post("/orders", async (req, res) => {
    const parsed = orderSchema.safeParse(req.body);
    if (!parsed.success) {
      res.status(400).json({ error: "Invalid order", details: z.flattenError(parsed.error).fieldErrors });
      return;
    }
    let items;
    try {
      items = await Promise.all(
        parsed.data.items.map(async (i) => ({ ...i, unitPrice: await prices(i.sku) })),
      );
    } catch {
      res.status(503).json({ error: "Catalog unavailable, try again" });
      return;
    }
    const unknown = items.filter((i) => i.unitPrice === null).map((i) => i.sku);
    if (unknown.length) {
      res.status(400).json({ error: `Unknown products: ${unknown.join(", ")}` });
      return;
    }
    const order = store.create(parsed.data.customer, items as { sku: string; quantity: number; unitPrice: number }[]);
    ordersCreated.inc();
    log("order created", { order_id: order.id, total: order.total });
    // The saga starts here: catalog reserves stock and answers with stock.reserved or stock.rejected.
    await bus.publish("order.created", { order_id: order.id, items: order.items.map(({ sku, quantity }) => ({ sku, quantity })) });
    res.status(202).location(`/orders/${order.id}`).json(order);
  });

  app.get("/orders/:id", (req, res) => {
    const order = store.get(req.params.id);
    if (!order) {
      res.status(404).json({ error: "Order not found" });
      return;
    }
    res.json(order);
  });

  return app;
}

/** Reacts to the catalog's answer and closes the saga. */
export async function wireSaga(store: OrderStore, bus: Bus) {
  await bus.subscribe("orders.stock-results", ["stock.reserved", "stock.rejected"], async (key, body) => {
    const status = key === "stock.reserved" ? "CONFIRMED" : "REJECTED";
    if (!store.settle(body.order_id, status)) return; // duplicate event: already settled
    const order = store.get(body.order_id)!;
    ordersSettled.inc({ status });
    sagaDuration.observe({ status }, (Date.now() - Date.parse(order.createdAt)) / 1000);
    log(`order ${status.toLowerCase()}`, { order_id: order.id });
    await bus.publish(status === "CONFIRMED" ? "order.confirmed" : "order.rejected", {
      order_id: order.id,
      customer: order.customer,
      total: order.total,
    });
  });
}
