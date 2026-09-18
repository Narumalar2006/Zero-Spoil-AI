"""
Freshness Agent.

Evaluates inventory freshness using batch-level shelf life and
contextual spoilage adjustments.

Safety rules:
    - Effective shelf life <= 0 -> expired / unsellable
    - Effective shelf life == 1 -> critical
    - Effective shelf life == 2 -> urgent
    - Effective shelf life > 2 -> fresh
"""

class FreshnessAgent:

    def __init__(self, conn):
        self.conn = conn

    def _batches(self, cycle_id, sku_id, store_id):
        rows = self.conn.execute(
            """
            SELECT qty, days_remaining
            FROM inventory_batches
            WHERE cycle_id=? AND sku_id=? AND store_id=?
            ORDER BY days_remaining ASC
            """,
            (cycle_id, sku_id, store_id),
        ).fetchall()

        return [
            {
                "qty": r["qty"],
                "days": r["days_remaining"],
            }
            for r in rows
        ]

    def _status(self, days):
        if days <= 0:
            return "expired"
        elif days == 1:
            return "critical"
        elif days == 2:
            return "urgent"
        else:
            return "fresh"

    def assess(
        self,
        cycle_id,
        sku_id,
        store_id,
        avg_daily,
        spoil_shift,
        forecast_3day,
    ):
        batches = self._batches(
            cycle_id,
            sku_id,
            store_id,
        )

        # ---------------------------------------------------------
        # Calculate effective shelf life after contextual spoilage.
        # Example:
        #   raw = 1 day
        #   heatwave spoil_shift = 1
        #   effective = 0 -> expired
        # ---------------------------------------------------------
        for batch in batches:
            batch["effective_days"] = max(
                0,
                batch["days"] - spoil_shift,
            )

            batch["status"] = self._status(
                batch["effective_days"]
            )

        stock = sum(
            b["qty"]
            for b in batches
        )

        # ---------------------------------------------------------
        # FIFO SELL-THROUGH ESTIMATION
        # ---------------------------------------------------------
        remaining_demand = max(
            0,
            forecast_3day,
        )

        waste_units = 0

        for batch in batches:

            effective_days = batch["effective_days"]

            # Expired inventory cannot be sold.
            if effective_days <= 0:
                waste_units += batch["qty"]
                continue

            sellable = min(
                batch["qty"],
                max(
                    0,
                    avg_daily * effective_days
                ),
                remaining_demand,
            )

            remaining_demand -= sellable

            # Inventory left after expected sell-through.
            leftover = batch["qty"] - sellable

            # Inventory with <=2 effective days remaining
            # is considered at risk if leftover remains.
            if effective_days <= 2:
                waste_units += leftover

        # ---------------------------------------------------------
        # EFFECTIVE FRESHNESS
        # ---------------------------------------------------------
        min_days = (
            batches[0]["effective_days"]
            if batches
            else 0
        )

        has_expired = any(
            b["effective_days"] <= 0
            for b in batches
        )

        has_critical = any(
            b["effective_days"] == 1
            for b in batches
        )

        freshness_status = self._status(
            min_days
        )

        # ---------------------------------------------------------
        # EVIDENCE
        # ---------------------------------------------------------
        evidence_parts = [
            f"{stock} unit(s) on hand",
            f"soonest effective shelf life {min_days} day(s)",
            f"freshness status: {freshness_status}",
            f"estimated FIFO waste risk: {round(waste_units)} unit(s)",
        ]

        if spoil_shift > 0:
            evidence_parts.append(
                f"contextual spoilage adjustment: -{spoil_shift} day"
            )

        if has_expired:
            evidence_parts.append(
                "expired/unsellable inventory detected"
            )

        elif has_critical:
            evidence_parts.append(
                "critical inventory detected: 1 effective day remaining"
            )

        return {
            "stock": stock,
            "waste_units": round(waste_units),
            "min_days": min_days,
            "freshness_status": freshness_status,
            "has_expired": has_expired,
            "has_critical": has_critical,
            "batches": batches,
            "evidence": "; ".join(evidence_parts),
        }