"""
Generates the synthetic dataset the whole system runs on:
catalog (SKUs, stores), 30 days of sales history, current inventory
batches, a waste log, and promotions. This stands in for POS,
WMS/inventory, and pricing/promo feeds ingested from a real retailer.
"""
import random
from datetime import datetime, timedelta
from database import get_conn

SKUS = [
    dict(id="MLK", name="Whole Milk 1L", category="Dairy", shelf_life_days=7, unit_cost=1.10, unit_price=2.20, base_daily=18),
    dict(id="YOG", name="Greek Yogurt 500g", category="Dairy", shelf_life_days=10, unit_cost=1.60, unit_price=3.40, base_daily=10),
    dict(id="BRD", name="Whole Wheat Bread", category="Bakery", shelf_life_days=4, unit_cost=0.90, unit_price=2.60, base_daily=14),
    dict(id="CRO", name="Butter Croissants 4pk", category="Bakery", shelf_life_days=3, unit_cost=1.80, unit_price=4.50, base_daily=9),
    dict(id="BAN", name="Bananas (kg)", category="Produce", shelf_life_days=5, unit_cost=0.55, unit_price=1.20, base_daily=20),
    dict(id="SPN", name="Spinach Bunch", category="Produce", shelf_life_days=3, unit_cost=0.85, unit_price=2.10, base_daily=8),
    dict(id="CHK", name="Chicken Breast (kg)", category="Meat", shelf_life_days=4, unit_cost=4.20, unit_price=7.90, base_daily=7),
    dict(id="BEF", name="Ground Beef (kg)", category="Meat", shelf_life_days=3, unit_cost=5.00, unit_price=8.90, base_daily=6),
]

STORES = [
    dict(id="DTN", name="Downtown", demand_factor=1.15, weather="Heatwave, 34C", event="Weekend farmers market nearby"),
    dict(id="RVR", name="Riverside", demand_factor=0.85, weather="Mild, 22C", event="No local event"),
    dict(id="HLC", name="Hillcrest", demand_factor=1.00, weather="Rain expected", event="School reopening week"),
]


def _category_context_factor(sku, store):
    f = 1.0
    if "market" in store["event"] and sku["category"] == "Produce":
        f *= 0.85
    if "reopening" in store["event"] and sku["category"] == "Bakery":
        f *= 1.25
    if "Heatwave" in store["weather"] and sku["category"] == "Dairy":
        f *= 1.15
    if "Rain" in store["weather"] and sku["category"] == "Produce":
        f *= 0.9
    return f


def ensure_catalog(conn):
    for s in STORES:
        conn.execute(
            "INSERT OR REPLACE INTO stores (id, name, demand_factor, weather, event) VALUES (?,?,?,?,?)",
            (s["id"], s["name"], s["demand_factor"], s["weather"], s["event"]),
        )
    for k in SKUS:
        conn.execute(
            "INSERT OR REPLACE INTO skus (id, name, category, shelf_life_days, unit_cost, unit_price) VALUES (?,?,?,?,?,?)",
            (k["id"], k["name"], k["category"], k["shelf_life_days"], k["unit_cost"], k["unit_price"]),
        )
    conn.commit()


def generate_cycle(conn, cycle_id, rng: random.Random):
    """Populate sales_history (30 days), inventory_batches, waste_log,
    and promotions for one simulation cycle. Returns nothing; writes to DB."""

    # clear cycle-scoped tables. Sales history, promotions, and batches are isolated per cycle.
    conn.execute("DELETE FROM inventory_batches WHERE cycle_id != ?", (cycle_id,))
    conn.execute("DELETE FROM promotions")
    conn.execute("DELETE FROM sales_history")

    today = datetime.utcnow().date()

    for sku in SKUS:
        for store in STORES:
            store_map = {s["id"]: s for s in STORES}
            store_row = store_map[store["id"]]
            ctx = _category_context_factor(sku, store_row)
            avg_daily = sku["base_daily"] * store["demand_factor"] * (0.88 + rng.random() * 0.24)

            # 30 days of sales history with mild day-to-day noise and a slight
            # upward/downward trend so a forecasting model has something real to fit
            trend = rng.uniform(-0.01, 0.015)
            for d in range(30, 0, -1):
                date = today - timedelta(days=d)
                day_factor = ctx * (1 + trend * (30 - d))
                noise = rng.uniform(0.75, 1.25)
                units = max(0, round(avg_daily * day_factor * noise))
                conn.execute(
                    "INSERT INTO sales_history (sku_id, store_id, sale_date, units_sold) VALUES (?,?,?,?)",
                    (sku["id"], store["id"], date.isoformat(), units),
                )
                if rng.random() < 0.12:
                    waste_units = rng.randint(0, 3)
                    if waste_units:
                        conn.execute(
                            "INSERT INTO waste_log (sku_id, store_id, waste_date, units_wasted, reason) VALUES (?,?,?,?,?)",
                            (sku["id"], store["id"], date.isoformat(), waste_units, "Expired on shelf"),
                        )

            # current inventory batches (1-2 batches, FIFO by days remaining)
            stock_days_cover = 1.6 + rng.random() * 3.2
            stock = max(2, round(avg_daily * stock_days_cover))
            if rng.random() < 0.65:
                batches = [(stock, 1 + rng.randrange(sku["shelf_life_days"]))]
            else:
                qa = max(1, round(stock * 0.4))
                qb = max(1, stock - qa)
                half = max(1, sku["shelf_life_days"] // 2)
                batches = [
                    (qa, 1 + rng.randrange(half)),
                    (qb, half + 1 + rng.randrange(max(1, sku["shelf_life_days"] - half))),
                ]
            batches.sort(key=lambda b: b[1])
            for i, (qty, days) in enumerate(batches):
                conn.execute(
                    "INSERT INTO inventory_batches (cycle_id, sku_id, store_id, batch_no, qty, days_remaining) VALUES (?,?,?,?,?,?)",
                    (cycle_id, sku["id"], store["id"], i, qty, days),
                )

            if rng.random() < 0.2:
                conn.execute(
                    "INSERT INTO promotions (sku_id, store_id, active, discount_pct) VALUES (?,?,?,?)",
                    (sku["id"], store["id"], 1, rng.choice([10, 15, 20])),
                )

    conn.commit()
