# Harvest Room — Zero-Waste Grocery Orchestrator

Agentic AI proof of concept for the "Zero-Waste Grocery" problem statement:
demand forecasting, freshness/spoilage-risk scoring, and a multi-agent
recommendation engine (Replenishment, Markdown, Transfer) coordinated by
an orchestrator that proposes exactly one reviewable action per SKU/store,
plus a planner dashboard and an evaluation report vs. a rule-based baseline.

## Architecture

```
zwg/
  backend/
    app.py                 Flask app: REST API + serves the frontend
    database.py             SQLite schema + connection helper
    data_generator.py       Synthetic dataset: catalog, 30-day sales history,
                             inventory batches, waste log, promotions
                             (stands in for POS / WMS / pricing feeds)
    agents/
      demand_agent.py       Recency-weighted moving-average forecaster
      freshness_agent.py    FIFO sell-through simulation -> spoilage risk
      replenishment_agent.py
      markdown_agent.py
      transfer_agent.py
      orchestrator.py       Runs all agents, applies priority rules
                             (transfer > markdown > replenish > hold),
                             persists one recommendation per SKU/store,
                             computes baseline-vs-POC evaluation metrics
    requirements.txt
  frontend/
    index.html, styles.css, app.js   Planner dashboard (talks to the API
                                      over fetch — no data lives client-side)
  data/                      SQLite database file lives here (auto-created)
```

## Run it

```bash
cd backend
pip install -r requirements.txt
python app.py
```

Open **http://localhost:5000** — that's the whole app, frontend and API
served from one process. On first run it builds the SQLite DB and generates
the first simulation cycle automatically.

## What's real vs. simulated

- **Real**: the Flask REST API, the SQLite persistence, the demand
  forecasting model (computed from stored 30-day sales history, not a fixed
  number), the FIFO freshness/spoilage simulation, the agent/orchestrator
  business-rule logic, the planner approve/modify/reject workflow and
  decision log, and the baseline-vs-POC evaluation — all computed
  server-side and fetched by the frontend over HTTP.
- **Simulated, as the brief allows**: the underlying retail data itself
  (sales, inventory, weather, events) is synthetically generated per
  cycle, standing in for POS/WMS/ERP feeds, per the brief's "use
  synthetic... and simulated retail-system interfaces."

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/cycle` | current cycle id + recommendation count |
| POST | `/api/cycle/new` | regenerate the dataset and re-run the orchestrator |
| GET | `/api/recommendations` | list recommendations (`?store=&category=&action=&status=`) |
| PATCH | `/api/recommendations/<id>` | `{"status":"approved"|"rejected"|"pending", "timing"?, "level"?}` |
| GET | `/api/logs/system` | simulated POS/WMS/ERP event feed for the current cycle |
| GET | `/api/logs/decisions` | planner decision log |
| GET | `/api/evaluation` | baseline vs. POC waste/stockout/margin metrics |

## Deploying for a public link

If your submission needs a public URL rather than localhost: this is a
plain Flask app with no external services, so it deploys as-is to
Render, Railway, PythonAnywhere, or Fly.io in a few minutes (set the
start command to `python backend/app.py`, or `gunicorn app:app` from
`backend/` for production).
