import { randomUUID } from "node:crypto";
import express, { type NextFunction, type Request, type Response } from "express";
import { createProxyMiddleware } from "http-proxy-middleware";
import { httpMetrics, metricsHandler } from "./metrics";
import { log } from "./telemetry";

export interface Services {
  orders: string;
  catalog: string;
  notifications: string;
}

/** Fixed-window rate limit per client IP (in memory: fine for one gateway instance). */
export function rateLimit(limit: number, windowMs: number) {
  const hits = new Map<string, { count: number; resetAt: number }>();
  return (req: Request, res: Response, next: NextFunction) => {
    const now = Date.now();
    const key = req.ip ?? "unknown";
    let entry = hits.get(key);
    if (!entry || entry.resetAt <= now) {
      entry = { count: 0, resetAt: now + windowMs };
      hits.set(key, entry);
    }
    entry.count++;
    res.setHeader("RateLimit-Limit", String(limit));
    res.setHeader("RateLimit-Remaining", String(Math.max(0, limit - entry.count)));
    if (entry.count > limit) {
      res.setHeader("Retry-After", String(Math.ceil((entry.resetAt - now) / 1000)));
      res.status(429).json({ error: "Too many requests" });
      return;
    }
    next();
  };
}

export function createGateway(services: Services, opts = { limit: 100, windowMs: 60_000 }) {
  const app = express();
  app.disable("x-powered-by");

  // Every request gets an ID that travels to the services, so one order can be traced across logs.
  app.use((req, res, next) => {
    const id = req.get("x-request-id") ?? randomUUID();
    req.headers["x-request-id"] = id;
    res.setHeader("x-request-id", id);
    const started = Date.now();
    res.on("finish", () => {
      log("request", { request_id: id, method: req.method, path: req.originalUrl, status: res.statusCode, ms: Date.now() - started });
    });
    next();
  });

  // Proxied requests have no Express route, so label them by the upstream prefix.
  const PREFIXES = ["/api/orders", "/api/products", "/api/notifications"];
  app.use(
    httpMetrics((req) => {
      const path = req.originalUrl.split("?")[0];
      if (path === "/health" || path === "/metrics") return path;
      return PREFIXES.find((p) => path === p || path.startsWith(p + "/")) ?? "unmatched";
    }),
  );
  app.get("/metrics", metricsHandler);

  app.get("/health", async (_req, res) => {
    const checks = await Promise.all(
      Object.entries(services).map(async ([name, url]) => {
        try {
          const r = await fetch(`${url}/health`, { signal: AbortSignal.timeout(1500) });
          return [name, r.ok ? "up" : "down"] as const;
        } catch {
          return [name, "down"] as const;
        }
      }),
    );
    const status = Object.fromEntries(checks);
    const healthy = checks.every(([, s]) => s === "up");
    res.status(healthy ? 200 : 503).json({ status: healthy ? "ok" : "degraded", services: status });
  });

  app.use("/api", rateLimit(opts.limit, opts.windowMs));

  const proxy = (target: string, prefix: string) =>
    createProxyMiddleware({
      target,
      changeOrigin: true,
      // "/" and "/?q" map to the bare prefix ("/orders", "/orders?q"), anything else is appended.
      pathRewrite: (path) => prefix + (path.startsWith("/?") || path === "/" ? path.slice(1) : path),
      proxyTimeout: 5000,
      on: {
        error: (_err, _req, res) => {
          const out = res as Response;
          if (!out.headersSent) out.status(502).json({ error: "Upstream service unavailable" });
        },
      },
    });

  app.use("/api/orders", proxy(services.orders, "/orders"));
  app.use("/api/products", proxy(services.catalog, "/products"));
  app.use("/api/notifications", proxy(services.notifications, "/notifications"));

  app.use((_req, res) => {
    res.status(404).json({ error: "Not found" });
  });
  return app;
}
