import { randomUUID } from "node:crypto";
import { DatabaseSync } from "node:sqlite";

export type OrderStatus = "PENDING" | "CONFIRMED" | "REJECTED";

export interface OrderItem {
  sku: string;
  quantity: number;
  unitPrice: number;
}

export interface Order {
  id: string;
  customer: string;
  items: OrderItem[];
  total: number;
  status: OrderStatus;
  createdAt: string;
}

/** Orders live in the orders service's own SQLite database (database-per-service). */
export class OrderStore {
  private db: DatabaseSync;

  constructor(path = ":memory:") {
    this.db = new DatabaseSync(path);
    this.db.exec(`CREATE TABLE IF NOT EXISTS orders (
      id TEXT PRIMARY KEY, customer TEXT, items TEXT, total INTEGER, status TEXT, created_at TEXT)`);
  }

  create(customer: string, items: OrderItem[]): Order {
    const order: Order = {
      id: randomUUID(),
      customer,
      items,
      total: items.reduce((sum, i) => sum + i.unitPrice * i.quantity, 0),
      status: "PENDING",
      createdAt: new Date().toISOString(),
    };
    this.db
      .prepare("INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?)")
      .run(order.id, order.customer, JSON.stringify(order.items), order.total, order.status, order.createdAt);
    return order;
  }

  get(id: string): Order | null {
    const row = this.db.prepare("SELECT * FROM orders WHERE id = ?").get(id) as Record<string, string | number> | undefined;
    if (!row) return null;
    return {
      id: String(row.id),
      customer: String(row.customer),
      items: JSON.parse(String(row.items)),
      total: Number(row.total),
      status: row.status as OrderStatus,
      createdAt: String(row.created_at),
    };
  }

  /** Moves a PENDING order to its final state. Returns false if it was already decided (redelivered event). */
  settle(id: string, status: Exclude<OrderStatus, "PENDING">): boolean {
    const result = this.db.prepare("UPDATE orders SET status = ? WHERE id = ? AND status = 'PENDING'").run(status, id);
    return Number(result.changes) > 0;
  }
}
