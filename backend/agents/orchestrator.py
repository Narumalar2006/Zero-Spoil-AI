"""
Orchestrator.

Runs Demand and Freshness agents for every SKU/store, then uses
Transfer, Markdown and Replenishment agents to generate one
recommendation per SKU/store.

Priority:
    expired -> markdown/urgent handling
    transfer -> markdown -> replenish -> hold
"""

import json
from datetime import datetime, timezone

from .demand_agent import DemandAgent
from .freshness_agent import FreshnessAgent
from .replenishment_agent import ReplenishmentAgent
from .markdown_agent import MarkdownAgent
from .transfer_agent import TransferAgent


def _context_factor_and_reasons(sku, store):
    f, reasons = 1.0, []

    if "market" in store["event"] and sku["category"] == "Produce":
        f *= 0.85
        reasons.append("farmers market nearby softens produce demand ~15%")

    if "reopening" in store["event"] and sku["category"] == "Bakery":
        f *= 1.25
        reasons.append("school reopening week lifts bakery demand ~25%")

    if "Heatwave" in store["weather"] and sku["category"] == "Dairy":
        f *= 1.15
        reasons.append("heatwave lifts chilled dairy demand ~15%")

    if "Rain" in store["weather"] and sku["category"] == "Produce":
        f *= 0.9
        reasons.append("rain forecast softens produce footfall ~10%")

    return f, reasons


def _spoil_shift(sku, store):
    if "Heatwave" in store["weather"] and sku["category"] in ("Dairy", "Meat"):
        return 1

    return 0


class Orchestrator:
    def __init__(self, conn):
        self.conn = conn
        self.demand = DemandAgent(conn)
        self.freshness = FreshnessAgent(conn)
        self.replenishment = ReplenishmentAgent()
        self.markdown = MarkdownAgent()
        self.transfer = TransferAgent()

    def run_cycle(self, cycle_id):
        conn = self.conn

        skus = [
            dict(r)
            for r in conn.execute("SELECT * FROM skus").fetchall()
        ]

        stores = [
            dict(r)
            for r in conn.execute("SELECT * FROM stores").fetchall()
        ]

        # ---------------------------------------------------------
        # PASS 1
        # Calculate demand + freshness for every SKU/store.
        # ---------------------------------------------------------
        metrics = {}

        for sku in skus:
            for store in stores:

                ctx_factor, ctx_reasons = _context_factor_and_reasons(
                    sku, store
                )

                d = self.demand.forecast(
                    sku["id"],
                    store["id"],
                    ctx_factor,
                    ctx_reasons,
                )

                spoil_shift = _spoil_shift(sku, store)

                fr = self.freshness.assess(
                    cycle_id,
                    sku["id"],
                    store["id"],
                    d["avg_daily"],
                    spoil_shift,
                    d["forecast_3day"],
                )

                unmet = max(
                    0,
                    round(d["forecast_3day"] - fr["stock"])
                )

                metrics[(sku["id"], store["id"])] = {
                    "sku": sku,
                    "store": store,
                    "demand": d,
                    "fresh": fr,
                    "unmet": unmet,
                }

        # Remove previous recommendations/events for this cycle.
        conn.execute(
            "DELETE FROM recommendations WHERE cycle_id=?",
            (cycle_id,),
        )

        conn.execute(
            "DELETE FROM system_events WHERE cycle_id=?",
            (cycle_id,),
        )

        # ---------------------------------------------------------
        # SYSTEM EVENTS
        # ---------------------------------------------------------
        events = []

        sample_keys = list(metrics.keys())

        for i, key in enumerate(sample_keys[:9]):

            m = metrics[key]
            kind = ["POS", "WMS", "ERP"][i % 3]

            if kind == "POS":
                msg = (
                    f"Sale recorded - {m['store']['name']}, "
                    f"{m['sku']['name']}, a few units."
                )

            elif kind == "WMS":
                msg = (
                    f"Inventory snapshot - {m['sku']['name']} @ "
                    f"{m['store']['name']}: "
                    f"{m['fresh']['stock']} on hand."
                )

            else:
                msg = (
                    f"Feed ingested - receipts & pricing sync "
                    f"for {m['store']['name']}."
                )

            events.append((kind, msg))

        events.append(
            (
                "ERP",
                f"Orchestrator cycle #{cycle_id} complete - "
                f"{len(metrics)} SKU/store combinations scored.",
            )
        )

        now = datetime.now(timezone.utc).isoformat()

        for kind, msg in events:
            conn.execute(
                """
                INSERT INTO system_events
                (cycle_id, ts, source, message)
                VALUES (?,?,?,?)
                """,
                (cycle_id, now, kind, msg),
            )

        # ---------------------------------------------------------
        # PASS 2
        # Generate proposals and choose final action.
        # ---------------------------------------------------------
        for (sku_id, store_id), m in metrics.items():

            sku = m["sku"]
            store = m["store"]
            d = m["demand"]
            fr = m["fresh"]
            unmet = m["unmet"]

            evidence = [
                (
                    f"Demand agent: forecasts {d['forecast_3day']} units "
                    f"over next 3 days (avg {d['avg_daily']}/day). "
                    + (
                        "; ".join(d["reasons"]) + "."
                        if d["reasons"]
                        else ""
                    )
                ),
                f"Freshness agent: {fr['evidence']}",
                (
                    f"Replenishment agent: projected shortfall "
                    f"of {unmet} unit(s) against forecast."
                ),
            ]

            # -----------------------------------------------------
            # TRANSFER
            # -----------------------------------------------------
            transfer_proposal = None

            if fr["waste_units"] >= 3:

                siblings = []

                for other_store in stores:

                    if other_store["id"] == store_id:
                        continue

                    om = metrics[(sku_id, other_store["id"])]

                    siblings.append(
                        {
                            "store_id": other_store["id"],
                            "store_name": other_store["name"],
                            "unmet": om["unmet"],
                            "avg_daily": om["demand"]["avg_daily"],
                            "forecast_3day": om["demand"]["forecast_3day"],
                            "min_days": om["fresh"]["min_days"],
                        }
                    )

                transfer_proposal = self.transfer.propose(
                    sku,
                    store["name"],
                    fr["waste_units"],
                    siblings,
                    fr,
                )

            # -----------------------------------------------------
            # MARKDOWN
            # -----------------------------------------------------
            markdown_proposal = self.markdown.propose(
                fr["waste_units"],
                fr["min_days"],
                sku,
            )

            # -----------------------------------------------------
            # REPLENISHMENT
            # -----------------------------------------------------
            replenish_proposal = self.replenishment.propose(
                d["forecast_3day"],
                fr["stock"],
                sku,
            )

            base_conf = d["confidence"]

            # =====================================================
            # EXPIRED INVENTORY SAFETY RULE
            # =====================================================
            if fr.get("has_expired", False):

                action = "hold"
                qty = 0
                level = "Expired stock - remove from sale"
                timing = "Immediate inventory quarantine"
                effect = (
                    "Expired inventory must not be transferred, "
                    "replenished or sold."
                )
                confidence = base_conf

                evidence.append(
                    "Safety rule: expired inventory detected. "
                    "Transfer blocked automatically."
                )

            # =====================================================
            # NORMAL OR AT-RISK INVENTORY
            # =====================================================
            elif transfer_proposal:

                action = "transfer"
                qty = transfer_proposal["qty"]
                level = transfer_proposal["level"]
                timing = transfer_proposal["timing"]
                effect = transfer_proposal["effect"]
                confidence = min(97, base_conf + 6)

                evidence.append(
                    f"Transfer agent: {transfer_proposal['evidence']}"
                )

            elif markdown_proposal:

                action = "markdown"
                qty = markdown_proposal["qty"]
                level = markdown_proposal["level"]
                timing = markdown_proposal["timing"]
                effect = markdown_proposal["effect"]
                confidence = base_conf

                evidence.append(
                    f"Markdown agent: {markdown_proposal['evidence']}"
                )

            elif replenish_proposal:

                action = "replenish"
                qty = replenish_proposal["qty"]
                level = f"{qty} units"
                timing = replenish_proposal["timing"]
                effect = replenish_proposal["effect"]
                confidence = base_conf

                evidence.append(
                    f"Replenishment agent: "
                    f"{replenish_proposal['evidence']}"
                )

            else:

                action = "hold"
                qty = 0
                level = "-"
                timing = "Continue monitoring"
                effect = (
                    "Stock and freshness are within a safe range; "
                    "no action needed this cycle."
                )
                confidence = max(55, base_conf - 4)

            # -----------------------------------------------------
            # SAVE RECOMMENDATION
            # -----------------------------------------------------
            conn.execute(
                """
                INSERT INTO recommendations
                (
                    cycle_id,
                    sku_id,
                    store_id,
                    forecast_3day,
                    stock,
                    min_days_remaining,
                    waste_units,
                    unmet_units,
                    action,
                    timing,
                    level,
                    qty,
                    confidence,
                    effect,
                    evidence_json,
                    status,
                    modified
                )
                VALUES
                (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'pending', 0)
                """,
                (
                    cycle_id,
                    sku_id,
                    store_id,
                    d["forecast_3day"],
                    fr["stock"],
                    fr["min_days"],
                    fr["waste_units"],
                    unmet,
                    action,
                    timing,
                    level,
                    qty,
                    confidence,
                    effect,
                    json.dumps(evidence),
                ),
            )

        conn.commit()

    def evaluate(self, cycle_id):
        """
        Baseline vs orchestrated POC evaluation.

        Baseline:
            - Uses the inventory risk observed before agent actions.
            - No inter-store transfer.
            - Existing demand/freshness risk remains unchanged.

        POC:
            - Applies the actions generated by the orchestrator.
            - Transfer reduces waste at the source and stockout at destination.
            - Replenishment reduces modeled stockout.
            - Markdown prevents modeled waste for the affected quantity.
            - Hold leaves the modeled risk unchanged.

        NOTE:
            These are simulated POC metrics based on the current cycle's
            inventory, demand, freshness and generated decisions. They are
            not real-world deployment results.
        """

        rows = [
            dict(r)
            for r in self.conn.execute(
                """
                SELECT *
                FROM recommendations
                WHERE cycle_id=?
                """,
                (cycle_id,),
            ).fetchall()
        ]

        # ---------------------------------------------------------
        # 1. BASELINE
        # ---------------------------------------------------------
        baseline_waste = 0
        baseline_stockout = 0

        for r in rows:
            baseline_waste += max(
                0,
                r["waste_units"],
            )

            baseline_stockout += max(
                0,
                r["unmet_units"],
            )

        # ---------------------------------------------------------
        # 2. SIMULATE AI ACTIONS
        # ---------------------------------------------------------
        poc_waste = 0
        poc_stockout = 0
        margin_protected = 0

        action_effects = {
            "transfer": 0,
            "markdown": 0,
            "replenish": 0,
            "hold": 0,
        }

        # Track mutable inventory risks across stores and SKUs.
        waste_map = {
            (r["sku_id"], r["store_id"]): max(0, r["waste_units"])
            for r in rows
        }
        stockout_map = {
            (r["sku_id"], r["store_id"]): max(0, r["unmet_units"])
            for r in rows
        }

        # Store lookup helpers to identify destination stores for transfers.
        stores = self.conn.execute("SELECT id, name FROM stores").fetchall()
        store_name_to_id = {s["name"]: s["id"] for s in stores}

        # SKU margin lookup helper.
        skus = self.conn.execute("SELECT id, unit_price, unit_cost FROM skus").fetchall()
        sku_margins = {s["id"]: s["unit_price"] - s["unit_cost"] for s in skus}

        # --- PHASE 1: TRANSFERS ---
        # Transfers are processed first so surplus inventory from source stores
        # satisfies destination store unmet demand before local actions take effect.
        for r in rows:
            if r["action"] != "transfer":
                continue

            sku_id = r["sku_id"]
            src_store_id = r["store_id"]
            src_key = (sku_id, src_store_id)
            qty = max(0, r["qty"])
            waste = waste_map.get(src_key, 0)

            # Identify the destination store
            dest_store_id = None
            if " -> " in r.get("level", ""):
                target_name = r["level"].split(" -> ")[-1].strip()
                dest_store_id = store_name_to_id.get(target_name)

            if not dest_store_id:
                # Fallback: check effect text or evidence JSON
                effect_text = r.get("effect", "")
                evidence_text = r.get("evidence_json", "")
                for name, s_id in store_name_to_id.items():
                    if s_id != src_store_id and (name in effect_text or name in evidence_text):
                        dest_store_id = s_id
                        break

            if not dest_store_id:
                # Fallback: sibling store with unmet demand for this SKU
                candidates = [
                    cand for cand in rows
                    if cand["sku_id"] == sku_id and cand["store_id"] != src_store_id and cand["unmet_units"] > 0
                ]
                if candidates:
                    candidates.sort(key=lambda s: -s["unmet_units"])
                    dest_store_id = candidates[0]["store_id"]

            dest_key = (sku_id, dest_store_id) if dest_store_id else None
            dest_unmet = stockout_map.get(dest_key, 0) if dest_key else 0

            transferred = min(qty, waste, dest_unmet)

            if transferred > 0:
                waste_map[src_key] = max(0, waste_map[src_key] - transferred)
                stockout_map[dest_key] = max(0, stockout_map[dest_key] - transferred)
                margin_protected += transferred * sku_margins.get(sku_id, 0)
                action_effects["transfer"] += transferred

        # --- PHASE 2: LOCAL ACTIONS (MARKDOWN, REPLENISH, HOLD) ---
        for r in rows:
            action = r["action"]
            key = (r["sku_id"], r["store_id"])
            qty = max(0, r["qty"])

            if action == "transfer":
                continue

            elif action == "markdown":
                curr_waste = waste_map.get(key, 0)
                markdown_qty = min(qty, curr_waste)
                waste_map[key] = max(0, curr_waste - markdown_qty)
                action_effects["markdown"] += markdown_qty

            elif action == "replenish":
                curr_stockout = stockout_map.get(key, 0)
                replenished = min(qty, curr_stockout)
                stockout_map[key] = max(0, curr_stockout - replenished)
                action_effects["replenish"] += replenished

            else:
                action_effects["hold"] += 1

        poc_waste = sum(waste_map.values())
        poc_stockout = sum(stockout_map.values())

        # ---------------------------------------------------------
        # 3. IMPACT METRICS
        # ---------------------------------------------------------

        waste_reduction = max(
            0,
            baseline_waste - poc_waste,
        )

        stockout_reduction = max(
            0,
            baseline_stockout - poc_stockout,
        )

        waste_reduction_pct = (
            (waste_reduction / baseline_waste) * 100
            if baseline_waste > 0
            else 0
        )

        stockout_reduction_pct = (
            (stockout_reduction / baseline_stockout) * 100
            if baseline_stockout > 0
            else 0
        )

        # ---------------------------------------------------------
        # 4. RETURN TRANSPARENT EVALUATION
        # ---------------------------------------------------------

        return {
            "methodology": {
                "baseline": (
                    "Observed cycle-level waste and unmet demand "
                    "before applying agent decisions."
                ),
                "poc": (
                    "Simulated outcome after applying the generated "
                    "transfer, markdown, replenishment and hold actions."
                ),
                "type": "simulated_poc",
                "real_world_result": False,
            },

            "baseline": {
                "waste": round(baseline_waste),
                "stockout": round(baseline_stockout),
            },

            "poc": {
                "waste": round(poc_waste),
                "stockout": round(poc_stockout),
                "margin_protected": round(
                    margin_protected
                ),
            },

            "impact": {
                "waste_reduction_units": round(
                    waste_reduction
                ),
                "waste_reduction_percent": round(
                    waste_reduction_pct,
                    1,
                ),
                "stockout_reduction_units": round(
                    stockout_reduction
                ),
                "stockout_reduction_percent": round(
                    stockout_reduction_pct,
                    1,
                ),
            },

            "actions": {
                "transfer_units": round(
                    action_effects["transfer"]
                ),
                "markdown_units": round(
                    action_effects["markdown"]
                ),
                "replenishment_units": round(
                    action_effects["replenish"]
                ),
                "hold_recommendations": (
                    action_effects["hold"]
                ),
            },
        }