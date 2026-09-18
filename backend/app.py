"""
Zero-Waste Grocery - Flask backend.

Run with:  python app.py
Then open: http://localhost:5000

Endpoints:
  GET  /api/cycle                 -> current cycle id + summary
  POST /api/cycle/new             -> regenerate dataset, run the orchestrator, start a new cycle
  GET  /api/recommendations       -> list recommendations for the current cycle (filterable)
  PATCH /api/recommendations/<id> -> approve / reject / modify a recommendation
  GET  /api/logs/system           -> simulated POS/WMS/ERP event feed for the current cycle
  GET  /api/logs/decisions        -> planner decision log
  GET  /api/evaluation            -> baseline vs POC metrics for the current cycle
"""
import json
import os
import random
import sys
from datetime import datetime, timezone

from flask import Flask, jsonify, request, send_from_directory

sys.path.insert(0, os.path.dirname(__file__))
from database import init_db, get_conn  # noqa: E402
import data_generator as gen  # noqa: E402
from agents.orchestrator import Orchestrator  # noqa: E402

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")

app = Flask(__name__, static_folder=None)

STATE = {"cycle_id": None, "seed": 20260918}


def run_new_cycle():
    conn = get_conn()
    gen.ensure_catalog(conn)
    cur = conn.execute(
        "INSERT INTO cycles (created_at) VALUES (?)",
        (datetime.now(timezone.utc).isoformat(),),
    )
    cycle_id = cur.lastrowid
    conn.commit()

    STATE["seed"] += 7919
    rng = random.Random(STATE["seed"])
    gen.generate_cycle(conn, cycle_id, rng)

    orch = Orchestrator(conn)
    orch.run_cycle(cycle_id)
    conn.close()

    STATE["cycle_id"] = cycle_id
    return cycle_id


@app.route("/")
def index():
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.route("/<path:path>")
def static_files(path):
    return send_from_directory(FRONTEND_DIR, path)


@app.route("/api/cycle", methods=["GET"])
def get_cycle():
    if STATE["cycle_id"] is None:
        run_new_cycle()
    conn = get_conn()
    count = conn.execute(
        "SELECT COUNT(*) c FROM recommendations WHERE cycle_id=?", (STATE["cycle_id"],)
    ).fetchone()["c"]
    conn.close()
    return jsonify({"cycle_id": STATE["cycle_id"], "recommendation_count": count})


@app.route("/api/cycle/new", methods=["POST"])
def new_cycle():
    cycle_id = run_new_cycle()
    return jsonify({"cycle_id": cycle_id})


@app.route("/api/recommendations", methods=["GET"])
def list_recommendations():
    if STATE["cycle_id"] is None:
        run_new_cycle()
    conn = get_conn()
    q = "SELECT r.*, s.name as sku_name, s.category as category, st.name as store_name " \
        "FROM recommendations r JOIN skus s ON s.id=r.sku_id JOIN stores st ON st.id=r.store_id " \
        "WHERE r.cycle_id=?"
    params = [STATE["cycle_id"]]
    for field, col in (("store", "r.store_id"), ("category", "s.category"),
                        ("action", "r.action"), ("status", "r.status")):
        val = request.args.get(field)
        if val and val != "all":
            q += f" AND {col}=?"
            params.append(val)
    rows = conn.execute(q, params).fetchall()
    conn.close()

    out = []
    for r in rows:
        d = dict(r)
        d["evidence"] = json.loads(d.pop("evidence_json"))
        out.append(d)
    return jsonify(out)


@app.route("/api/recommendations/<int:rec_id>", methods=["PATCH"])
def update_recommendation(rec_id):
    body = request.get_json(force=True) or {}
    status = body.get("status")
    if status not in ("approved", "rejected", "pending"):
        return jsonify({"error": "status must be approved, rejected or pending"}), 400

    conn = get_conn()
    rec = conn.execute("SELECT * FROM recommendations WHERE id=?", (rec_id,)).fetchone()
    if not rec:
        conn.close()
        return jsonify({"error": "not found"}), 404

    modified = 0
    timing = rec["timing"]
    level = rec["level"]
    if status == "approved" and ("timing" in body or "level" in body):
        timing = body.get("timing", timing)
        level = body.get("level", level)
        modified = 1

    conn.execute(
        "UPDATE recommendations SET status=?, timing=?, level=?, modified=? WHERE id=?",
        (status, timing, level, modified, rec_id),
    )

    sku = conn.execute("SELECT name FROM skus WHERE id=?", (rec["sku_id"],)).fetchone()
    store = conn.execute("SELECT name FROM stores WHERE id=?", (rec["store_id"],)).fetchone()
    verb = {"approved": "Approved", "rejected": "Rejected", "pending": "Reopened for review"}[status]
    suffix = f" (now {level}, {timing})" if modified else ""
    msg = f"{verb} {rec['action']} - {sku['name']} @ {store['name']}{suffix}."
    conn.execute(
        "INSERT INTO decision_log (recommendation_id, ts, message) VALUES (?,?,?)",
        (rec_id, datetime.now(timezone.utc).isoformat(), msg),
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/logs/system", methods=["GET"])
def system_log():
    if STATE["cycle_id"] is None:
        run_new_cycle()
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM system_events WHERE cycle_id=? ORDER BY id ASC", (STATE["cycle_id"],)
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/logs/decisions", methods=["GET"])
def decision_log():
    conn = get_conn()
    rows = conn.execute(
        """SELECT dl.*, r.sku_id, r.store_id FROM decision_log dl
           JOIN recommendations r ON r.id = dl.recommendation_id
           WHERE r.cycle_id=? ORDER BY dl.id DESC""",
        (STATE["cycle_id"],),
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/evaluation", methods=["GET"])
def evaluation():
    if STATE["cycle_id"] is None:
        run_new_cycle()
    conn = get_conn()
    orch = Orchestrator(conn)
    result = orch.evaluate(STATE["cycle_id"])
    conn.close()
    return jsonify(result)


if __name__ == "__main__":
    init_db(fresh=True)
    run_new_cycle()
    app.run(host="0.0.0.0", port=5000, debug=True)
