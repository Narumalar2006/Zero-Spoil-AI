"""
Transfer Agent.

Moves at-risk inventory to sibling stores only when the inventory is
still safe to transfer.

Safety rules:
    - Expired inventory is never transferred.
    - Near-expiry inventory is transferred only against demonstrated
      destination demand.
    - Critical stock (1 day) is capped by destination 1-day demand.
    - Urgent stock (2 days) is capped by destination 2-day demand.
"""


THRESHOLD = 3


class TransferAgent:
    def propose(
        self,
        sku,
        this_store,
        waste_units,
        sibling_metrics,
        freshness=None,
    ):
        freshness = freshness or {}

        # ---------------------------------------------------------
        # SAFETY RULE 1
        # Never transfer expired / unsellable inventory.
        # ---------------------------------------------------------
        if freshness.get("has_expired", False):
            return None

        # ---------------------------------------------------------
        # SAFETY RULE 2
        # No meaningful waste risk -> no transfer.
        # ---------------------------------------------------------
        if waste_units < THRESHOLD:
            return None

        min_days = freshness.get("min_days", 0)

        # ---------------------------------------------------------
        # SAFETY RULE 3
        # Effective shelf life <= 0 is unsafe.
        # ---------------------------------------------------------
        if min_days <= 0:
            return None

        # ---------------------------------------------------------
        # Find stores with demonstrated unmet demand.
        # ---------------------------------------------------------
        candidates = [
            s
            for s in sibling_metrics
            if s["unmet"] >= THRESHOLD
            and s.get("avg_daily", 0) > 0
        ]

        if not candidates:
            return None

        # Highest unmet demand first.
        candidates.sort(
            key=lambda s: -s["unmet"]
        )

        target = candidates[0]

        # ---------------------------------------------------------
        # DESTINATION CONSUMPTION WINDOW
        #
        # 1 day remaining -> only today's expected demand
        # 2 days remaining -> up to two days expected demand
        # >2 days remaining -> demonstrated unmet demand
        # ---------------------------------------------------------
        if min_days == 1:
            destination_capacity = max(
                0,
                round(target["avg_daily"])
            )

            timing = (
                "Urgent shuttle — destination must use stock "
                "before expiry"
            )

            risk_note = (
                f"Only 1 day of shelf life remains; transfer is "
                f"capped at approximately {destination_capacity} "
                f"unit(s) of destination one-day demand."
            )

        elif min_days == 2:
            destination_capacity = max(
                0,
                round(target["avg_daily"] * 2)
            )

            timing = (
                "Next inter-store shuttle, today 2:00 PM"
            )

            risk_note = (
                f"2 days of shelf life remain; transfer is capped "
                f"at approximately {destination_capacity} unit(s) "
                f"of destination two-day demand."
            )

        else:
            destination_capacity = target["unmet"]

            timing = (
                "Next inter-store shuttle, today 2:00 PM"
            )

            risk_note = (
                "Inventory has sufficient remaining shelf life "
                "for transfer against demonstrated demand."
            )

        # ---------------------------------------------------------
        # FINAL SAFE TRANSFER QUANTITY
        # ---------------------------------------------------------
        qty = min(
            waste_units,
            target["unmet"],
            destination_capacity,
        )

        if qty <= 0:
            return None

        margin = round(
            qty
            * (sku["unit_price"] - sku["unit_cost"])
        )

        return {
            "qty": qty,
            "target_store_id": target["store_id"],
            "target_store_name": target["store_name"],
            "timing": timing,
            "level": (
                f"{qty} units -> {target['store_name']}"
            ),
            "effect": (
                f"Prevents ~{qty} units of waste at "
                f"{this_store} and covers ~{qty} units "
                f"of shortfall at {target['store_name']} "
                f"(~${margin} margin protected)."
            ),
            "evidence": (
                f"{target['store_name']} shows "
                f"{target['unmet']} unit(s) unmet demand "
                f"for the same SKU this cycle. "
                f"Destination average demand is "
                f"{round(target['avg_daily'], 1)} unit(s)/day. "
                f"{risk_note}"
            ),
        }