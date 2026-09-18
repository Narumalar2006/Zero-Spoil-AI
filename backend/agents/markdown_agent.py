"""
Markdown Agent.
Proposes a tiered markdown when units are projected to go unsold
before they expire, tiering the discount by how little time is left.
"""

THRESHOLD = 3


class MarkdownAgent:
    def propose(self, waste_units, min_days, sku):
        if waste_units < THRESHOLD:
            return None
        tier = 40 if min_days <= 1 else 25 if min_days <= 2 else 15
        recovered = round(waste_units * sku["unit_price"] * (1 - tier / 100))
        full_loss = round(waste_units * sku["unit_cost"])
        timing = "Apply now, valid till close" if min_days <= 1 else "Apply this evening"
        return {
            "qty": waste_units,
            "level": f"{tier}% off",
            "timing": timing,
            "effect": f"Recovers ~{waste_units} units before expiry at {tier}% off (~${recovered} salvaged vs ~${full_loss} lost if written off).",
            "evidence": f"Soonest batch expires in {min_days} day(s) -> tiered markdown of {tier}%.",
        }
