"""
SQLite database layer for the Zero-Waste Grocery orchestrator.
Owns schema creation and gives every other module a single place
to get a connection from.
"""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "zwg.sqlite3")
DB_PATH = os.path.abspath(DB_PATH)


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS stores (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    demand_factor REAL NOT NULL,
    weather TEXT NOT NULL,
    event TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS skus (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    shelf_life_days INTEGER NOT NULL,
    unit_cost REAL NOT NULL,
    unit_price REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS sales_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sku_id TEXT NOT NULL REFERENCES skus(id),
    store_id TEXT NOT NULL REFERENCES stores(id),
    sale_date TEXT NOT NULL,
    units_sold INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS inventory_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id INTEGER NOT NULL,
    sku_id TEXT NOT NULL REFERENCES skus(id),
    store_id TEXT NOT NULL REFERENCES stores(id),
    batch_no INTEGER NOT NULL,
    qty INTEGER NOT NULL,
    days_remaining INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS waste_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sku_id TEXT NOT NULL REFERENCES skus(id),
    store_id TEXT NOT NULL REFERENCES stores(id),
    waste_date TEXT NOT NULL,
    units_wasted INTEGER NOT NULL,
    reason TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS promotions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sku_id TEXT NOT NULL REFERENCES skus(id),
    store_id TEXT NOT NULL REFERENCES stores(id),
    active INTEGER NOT NULL,
    discount_pct REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS cycles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS recommendations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id INTEGER NOT NULL REFERENCES cycles(id),
    sku_id TEXT NOT NULL REFERENCES skus(id),
    store_id TEXT NOT NULL REFERENCES stores(id),
    forecast_3day REAL NOT NULL,
    stock INTEGER NOT NULL,
    min_days_remaining INTEGER NOT NULL,
    waste_units INTEGER NOT NULL,
    unmet_units INTEGER NOT NULL,
    action TEXT NOT NULL,
    timing TEXT NOT NULL,
    level TEXT NOT NULL,
    qty INTEGER NOT NULL,
    confidence INTEGER NOT NULL,
    effect TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    modified INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS system_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id INTEGER NOT NULL,
    ts TEXT NOT NULL,
    source TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS decision_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recommendation_id INTEGER NOT NULL REFERENCES recommendations(id),
    ts TEXT NOT NULL,
    message TEXT NOT NULL
);
"""


def init_db(fresh=False):
    if fresh and os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = get_conn()
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
