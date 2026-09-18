"""
Demand Agent.
Forecasts next-3-day demand per SKU/store from the persisted 30-day
sales_history table: a recency-weighted moving average, adjusted for
weather/local-event context, with a confidence derived from how
volatile recent sales have been.
"""
import statistics


class DemandAgent:
    def __init__(self, conn):
        self.conn = conn

    def _recent_sales(self, sku_id, store_id, days=14):
        rows = self.conn.execute(
            """SELECT units_sold FROM sales_history
               WHERE sku_id=? AND store_id=?
               ORDER BY sale_date DESC LIMIT ?""",
            (sku_id, store_id, days),
        ).fetchall()
        return [r["units_sold"] for r in rows][::-1]  # oldest -> newest

    def forecast(self, sku_id, store_id, context_factor, context_reasons):
        series = self._recent_sales(sku_id, store_id, days=14)
        if not series:
            return {"forecast_3day": 0, "avg_daily": 0, "confidence": 50, "reasons": context_reasons}

        # recency-weighted moving average: linearly increasing weights
        weights = list(range(1, len(series) + 1))
        weighted_avg = sum(w * v for w, v in zip(weights, series)) / sum(weights)

        avg_daily = weighted_avg * context_factor
        forecast_3day = avg_daily * 3

        # confidence: lower coefficient of variation in recent sales -> higher confidence
        mean = statistics.mean(series) or 1
        stdev = statistics.pstdev(series) if len(series) > 1 else 0
        cov = stdev / mean if mean else 0
        confidence = max(55, min(96, round(92 - cov * 60)))

        reasons = list(context_reasons)
        reasons.insert(
            0,
            f"Recency-weighted average of last {len(series)} days of sales is {weighted_avg:.1f}/day"
            + (f", context-adjusted to {avg_daily:.1f}/day" if abs(context_factor - 1) > 0.001 else ""),
        )

        return {
            "forecast_3day": round(forecast_3day, 1),
            "avg_daily": round(avg_daily, 2),
            "confidence": confidence,
            "reasons": reasons,
        }
