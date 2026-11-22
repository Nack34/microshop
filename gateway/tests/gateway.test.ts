import { createServer, type Server } from "node:http";
import type { AddressInfo } from "node:net";
import request from "supertest";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { createGateway } from "../src/app";

let upstream: Server;
let base: string;
const seen: { url?: string; id?: string }[] = [];

beforeAll(async () => {
  upstream = createServer((req, res) => {
    seen.push({ url: req.url, id: req.headers["x-request-id"] as string });
    res.setHeader("content-type", "application/json");
    res.end(JSON.stringify({ path: req.url, status: "ok" }));
  });
  await new Promise<void>((r) => upstream.listen(0, r));
  base = `http://127.0.0.1:${(upstream.address() as AddressInfo).port}`;
});
afterAll(() => upstream.close());

describe("gateway", () => {
  it("routes by prefix and rewrites paths", async () => {
    const app = createGateway({ orders: base, catalog: base, notifications: base });
    expect((await request(app).get("/api/products/KB-01")).body.path).toBe("/products/KB-01");
    expect((await request(app).get("/api/orders/abc")).body.path).toBe("/orders/abc");
    expect((await request(app).get("/api/notifications?to=a@b.com")).body.path).toBe("/notifications?to=a@b.com");
  });

  it("propagates or creates a request id", async () => {
    const app = createGateway({ orders: base, catalog: base, notifications: base });
    const res = await request(app).get("/api/products").set("x-request-id", "trace-123");
    expect(res.headers["x-request-id"]).toBe("trace-123");
    expect(seen.at(-1)?.id).toBe("trace-123");
    const fresh = await request(app).get("/api/products");
    expect(fresh.headers["x-request-id"]).toMatch(/^[0-9a-f-]{36}$/);
  });

  it("reports aggregated health and degrades when a service is down", async () => {
    expect((await request(createGateway({ orders: base, catalog: base, notifications: base })).get("/health")).body).toEqual({
      status: "ok",
      services: { orders: "up", catalog: "up", notifications: "up" },
    });
    const res = await request(createGateway({ orders: base, catalog: "http://127.0.0.1:1", notifications: base })).get("/health");
    expect(res.status).toBe(503);
    expect(res.body.services.catalog).toBe("down");
  });

  it("answers 502 when the upstream is unreachable", async () => {
    const app = createGateway({ orders: "http://127.0.0.1:1", catalog: base, notifications: base });
    const res = await request(app).get("/api/orders/x");
    expect(res.status).toBe(502);
  });

  it("rate limits per client", async () => {
    const app = createGateway({ orders: base, catalog: base, notifications: base }, { limit: 2, windowMs: 60_000 });
    await request(app).get("/api/products");
    await request(app).get("/api/products");
    const blocked = await request(app).get("/api/products");
    expect(blocked.status).toBe(429);
    expect(blocked.headers["retry-after"]).toBeDefined();
  });

  it("exposes metrics labelled by upstream prefix", async () => {
    const app = createGateway({ orders: base, catalog: base, notifications: base });
    await request(app).get("/api/products/KB-01?x=1");
    const text = (await request(app).get("/metrics")).text;
    expect(text).toContain('http_request_duration_seconds_count{method="GET",route="/api/products",status_code="200"}');
    expect(text).not.toContain("KB-01");
  });

  it("returns 404 for unknown routes", async () => {
    const app = createGateway({ orders: base, catalog: base, notifications: base });
    expect((await request(app).get("/nope")).status).toBe(404);
  });
});
