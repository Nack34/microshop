"""Catalog data: products and stock reservations, in SQLite (each service owns its own database)."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass

SEED = [
    ("KB-01", "Mechanical keyboard", 82000, 10),
    ("MS-01", "Wireless mouse", 15000, 25),
    ("MN-27", "27-inch monitor", 320000, 2),
]


@dataclass
class Product:
    sku: str
    name: str
    price: int
    stock: int


class OutOfStock(Exception):
    def __init__(self, sku: str, available: int, requested: int):
        super().__init__(f"{sku}: only {available} left, {requested} requested")
        self.sku = sku


class CatalogStore:
    def __init__(self, path: str = ":memory:") -> None:
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS products (sku TEXT PRIMARY KEY, name TEXT, price INTEGER, stock INTEGER);
            CREATE TABLE IF NOT EXISTS reservations (order_id TEXT PRIMARY KEY, outcome TEXT);
            """
        )
        if not self.db.execute("SELECT 1 FROM products LIMIT 1").fetchone():
            self.db.executemany("INSERT INTO products VALUES (?, ?, ?, ?)", SEED)

    @contextmanager
    def _tx(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def products(self) -> list[Product]:
        return [Product(**dict(r)) for r in self.db.execute("SELECT * FROM products ORDER BY sku")]

    def get(self, sku: str) -> Product | None:
        row = self.db.execute("SELECT * FROM products WHERE sku = ?", (sku,)).fetchone()
        return Product(**dict(row)) if row else None

    def reserve(self, order_id: str, items: list[dict]) -> str:
        """Reserve all items or none. Idempotent per order: a redelivered event returns the first outcome."""
        previous = self.db.execute("SELECT outcome FROM reservations WHERE order_id = ?", (order_id,)).fetchone()
        if previous:
            return previous["outcome"]
        try:
            with self._tx():
                for item in items:
                    row = self.db.execute("SELECT stock FROM products WHERE sku = ?", (item["sku"],)).fetchone()
                    available = row["stock"] if row else 0
                    if available < item["quantity"]:
                        raise OutOfStock(item["sku"], available, item["quantity"])
                    self.db.execute("UPDATE products SET stock = stock - ? WHERE sku = ?", (item["quantity"], item["sku"]))
                self.db.execute("INSERT INTO reservations VALUES (?, 'reserved')", (order_id,))
            return "reserved"
        except OutOfStock:
            self.db.execute("INSERT OR IGNORE INTO reservations VALUES (?, 'rejected')", (order_id,))
            return "rejected"
