import json
import sqlite3
import unittest
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from database import SCHEMA
from agents.orchestrator import Orchestrator


class TestEvaluationTransfer(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

        # Populate stores
        self.conn.execute(
            "INSERT INTO stores (id, name, demand_factor, weather, event) VALUES (?,?,?,?,?)",
            ("DTN", "Downtown", 1.15, "Mild", "None"),
        )
        self.conn.execute(
            "INSERT INTO stores (id, name, demand_factor, weather, event) VALUES (?,?,?,?,?)",
            ("RVR", "Riverside", 0.85, "Mild", "None"),
        )

        # Populate SKU: margin = 2.20 - 1.10 = 1.10
        self.conn.execute(
            "INSERT INTO skus (id, name, category, shelf_life_days, unit_cost, unit_price) VALUES (?,?,?,?,?,?)",
            ("MLK", "Whole Milk 1L", "Dairy", 7, 1.10, 2.20),
        )

        # Create cycle
        cur = self.conn.execute("INSERT INTO cycles (created_at) VALUES ('2026-09-19T00:00:00')")
        self.cycle_id = cur.lastrowid
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_transfer_evaluation_reduces_destination_stockout_and_source_waste(self):
        """
        DTN has 5 waste units and 0 unmet units, recommends transfer of 4 units to RVR.
        RVR has 0 waste units and 6 unmet units, recommends hold.
        Transfer should:
          - reduce DTN waste by 4 (5 -> 1)
          - reduce RVR stockout by 4 (6 -> 2)
          - protect margin: 4 * $1.10 = $4.40 -> $4
          - count 4 transfer units
        """
        # DTN row: source of transfer
        self.conn.execute(
            """
            INSERT INTO recommendations
            (cycle_id, sku_id, store_id, forecast_3day, stock, min_days_remaining,
             waste_units, unmet_units, action, timing, level, qty, confidence,
             effect, evidence_json, status, modified)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'pending', 0)
            """,
            (
                self.cycle_id, "MLK", "DTN", 10.0, 15, 2,
                5, 0, "transfer", "Today 2:00 PM", "4 units -> Riverside", 4, 90,
                "Prevents ~4 units of waste at Downtown and covers ~4 units of shortfall at Riverside (~$4 margin protected).",
                json.dumps(["Transfer agent: Riverside shows 6 unit(s) unmet demand"]),
            ),
        )

        # RVR row: destination of transfer
        self.conn.execute(
            """
            INSERT INTO recommendations
            (cycle_id, sku_id, store_id, forecast_3day, stock, min_days_remaining,
             waste_units, unmet_units, action, timing, level, qty, confidence,
             effect, evidence_json, status, modified)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'pending', 0)
            """,
            (
                self.cycle_id, "MLK", "RVR", 12.0, 6, 5,
                0, 6, "hold", "Continue monitoring", "-", 0, 80,
                "Stock and freshness are within a safe range.",
                json.dumps(["Demand agent: forecast 12 units"]),
            ),
        )
        self.conn.commit()

        orch = Orchestrator(self.conn)
        result = orch.evaluate(self.cycle_id)

        # Baseline asserts
        self.assertEqual(result["baseline"]["waste"], 5)
        self.assertEqual(result["baseline"]["stockout"], 6)

        # POC outcome asserts
        self.assertEqual(result["poc"]["waste"], 1)
        self.assertEqual(result["poc"]["stockout"], 2)
        self.assertEqual(result["poc"]["margin_protected"], 4)

        # Impact asserts
        self.assertEqual(result["impact"]["waste_reduction_units"], 4)
        self.assertEqual(result["impact"]["waste_reduction_percent"], 80.0)
        self.assertEqual(result["impact"]["stockout_reduction_units"], 4)
        self.assertEqual(result["impact"]["stockout_reduction_percent"], 66.7)

        # Action unit breakdown asserts
        self.assertEqual(result["actions"]["transfer_units"], 4)
        self.assertEqual(result["actions"]["hold_recommendations"], 1)

    def test_transfer_fallback_destination_matching(self):
        """
        Verify fallback matching when level text does not contain ' -> '.
        """
        self.conn.execute(
            """
            INSERT INTO recommendations
            (cycle_id, sku_id, store_id, forecast_3day, stock, min_days_remaining,
             waste_units, unmet_units, action, timing, level, qty, confidence,
             effect, evidence_json, status, modified)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'pending', 0)
            """,
            (
                self.cycle_id, "MLK", "DTN", 10.0, 15, 2,
                5, 0, "transfer", "Today 2:00 PM", "4 units", 4, 90,
                "Prevents ~4 units of waste at Downtown and covers ~4 units of shortfall at Riverside (~$4 margin protected).",
                json.dumps(["Transfer agent: Riverside shows 6 unit(s) unmet demand"]),
            ),
        )
        self.conn.execute(
            """
            INSERT INTO recommendations
            (cycle_id, sku_id, store_id, forecast_3day, stock, min_days_remaining,
             waste_units, unmet_units, action, timing, level, qty, confidence,
             effect, evidence_json, status, modified)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'pending', 0)
            """,
            (
                self.cycle_id, "MLK", "RVR", 12.0, 6, 5,
                0, 6, "hold", "Continue monitoring", "-", 0, 80,
                "Stock and freshness are within a safe range.",
                json.dumps([]),
            ),
        )
        self.conn.commit()

        orch = Orchestrator(self.conn)
        result = orch.evaluate(self.cycle_id)

        self.assertEqual(result["actions"]["transfer_units"], 4)
        self.assertEqual(result["poc"]["margin_protected"], 4)
        self.assertEqual(result["impact"]["waste_reduction_units"], 4)
        self.assertEqual(result["impact"]["stockout_reduction_units"], 4)


if __name__ == "__main__":
    unittest.main()
