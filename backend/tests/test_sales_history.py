import random
import sqlite3
import unittest
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from database import SCHEMA
import data_generator as gen
from agents.demand_agent import DemandAgent


class TestSalesHistoryIsolation(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        gen.ensure_catalog(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_cycle_generation_isolates_sales_history_without_duplicates(self):
        """
        Verify that running consecutive simulation cycles does not duplicate
        sales history records for the same calendar dates, ensuring DemandAgent
        evaluates exactly 14 distinct chronological days.
        """
        # Cycle 1
        rng1 = random.Random(1001)
        gen.generate_cycle(self.conn, cycle_id=1, rng=rng1)

        # 8 SKUs * 3 stores * 30 days = 720 rows
        cur = self.conn.execute("SELECT COUNT(*) as total FROM sales_history")
        self.assertEqual(cur.fetchone()["total"], 720)

        cur = self.conn.execute(
            "SELECT COUNT(*) as count, COUNT(DISTINCT sale_date) as distinct_dates "
            "FROM sales_history WHERE sku_id='MLK' AND store_id='DTN'"
        )
        row1 = cur.fetchone()
        self.assertEqual(row1["count"], 30)
        self.assertEqual(row1["distinct_dates"], 30)

        # Cycle 2
        rng2 = random.Random(2002)
        gen.generate_cycle(self.conn, cycle_id=2, rng=rng2)

        # Total rows must remain 720 (isolated/replaced), NOT accumulate to 1440
        cur = self.conn.execute("SELECT COUNT(*) as total FROM sales_history")
        self.assertEqual(cur.fetchone()["total"], 720)

        cur = self.conn.execute(
            "SELECT COUNT(*) as count, COUNT(DISTINCT sale_date) as distinct_dates "
            "FROM sales_history WHERE sku_id='MLK' AND store_id='DTN'"
        )
        row2 = cur.fetchone()
        self.assertEqual(row2["count"], 30)
        self.assertEqual(row2["distinct_dates"], 30)

        # Verify zero duplicate (sku_id, store_id, sale_date) tuples
        cur = self.conn.execute(
            """
            SELECT sku_id, store_id, sale_date, COUNT(*) as c
            FROM sales_history
            GROUP BY sku_id, store_id, sale_date
            HAVING COUNT(*) > 1
            """
        )
        duplicates = cur.fetchall()
        self.assertEqual(len(duplicates), 0, f"Found duplicate sales records: {duplicates}")

        # Verify DemandAgent receives exactly 14 distinct daily sales
        agent = DemandAgent(self.conn)
        series = agent._recent_sales("MLK", "DTN", days=14)
        self.assertEqual(len(series), 14)

        forecast = agent.forecast("MLK", "DTN", 1.0, [])
        self.assertGreater(forecast["forecast_3day"], 0)
        self.assertGreaterEqual(forecast["confidence"], 55)
        self.assertLessEqual(forecast["confidence"], 96)
        self.assertTrue(any("last 14 days" in r for r in forecast["reasons"]))


if __name__ == "__main__":
    unittest.main()
