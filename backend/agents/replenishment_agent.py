"""
Replenishment Agent.
Proposes a reorder when forecast demand outstrips on-hand stock,
with a small safety buffer.
"""

THRESHOLD = 3


class ReplenishmentAgent:
    def propose(self, forecast_3day, stock, sku):
        unmet = max(0, round(forecast_3day - stock))
        if unmet < THRESHOLD:
            return None
        qty = int(round(unmet * 1.1)) or 1
        amount_at_risk = round(unmet * sku["unit_price"])
        return {
            "qty": qty,
            "unmet": unmet,
            "timing": "Tomorrow 6:00 AM delivery slot",
            "effect": f"Avoids stockout of ~{unmet} units (~${amount_at_risk} at-risk lost sales) with a 10% safety buffer.",
            "evidence": f"Forecast exceeds on-hand stock by {unmet} unit(s); proposes {qty} units to close the gap plus buffer.",
        }
