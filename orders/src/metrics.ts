import type { NextFunction, Request, Response } from "express";
import client from "prom-client";

export const registry = new client.Registry();
client.collectDefaultMetrics({ register: registry });

const requestDuration = new client.Histogram({
  name: "http_request_duration_seconds",
  help: "HTTP request latency",
  labelNames: ["method", "route", "status_code"],
  buckets: [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5],
  registers: [registry],
});

/**
 * Records every request under a route *template* (e.g. /orders/:id), never the raw URL,
 * so the number of time series stays bounded.
 */
export function httpMetrics(routeOf: (req: Request) => string) {
  return (req: Request, res: Response, next: NextFunction) => {
    const end = requestDuration.startTimer();
    res.on("finish", () => {
      const route = routeOf(req);
      if (route !== "/metrics") end({ method: req.method, route, status_code: String(res.statusCode) });
    });
    next();
  };
}

export async function metricsHandler(_req: Request, res: Response) {
  res.setHeader("content-type", registry.contentType);
  res.send(await registry.metrics());
}

export const ordersCreated = new client.Counter({
  name: "shop_orders_created_total",
  help: "Orders accepted as PENDING",
  registers: [registry],
});

export const ordersSettled = new client.Counter({
  name: "shop_orders_settled_total",
  help: "Orders that reached a final state",
  labelNames: ["status"],
  registers: [registry],
});

export const sagaDuration = new client.Histogram({
  name: "shop_order_saga_duration_seconds",
  help: "Time from order creation to CONFIRMED/REJECTED",
  labelNames: ["status"],
  buckets: [0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30],
  registers: [registry],
});
