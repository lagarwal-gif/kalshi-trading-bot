"""
Kalshi trading dashboard.

Run this in a separate terminal alongside main.py:
    python dashboard.py

Opens at http://localhost:8000
"""

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request
import uvicorn
import db
import kalshi_client
import config

app = FastAPI(title="Kalshi Trading Dashboard")
templates = Jinja2Templates(directory="templates")


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/api/state")
def get_state():
    try:
        balance = kalshi_client.get_balance()
    except Exception as e:
        balance = None
        balance_error = str(e)
    else:
        balance_error = None

    try:
        positions = kalshi_client.get_positions()
        # Enrich positions with current market price
        enriched_positions = []
        for p in positions:
            try:
                market = kalshi_client.get_market(p["ticker"])
                p["yes_ask"] = market.get("yes_ask")
                p["no_ask"] = market.get("no_ask")
                p["title"] = market.get("title", p["ticker"])
                p["close_time"] = market.get("close_time", "")
            except Exception:
                p["yes_ask"] = None
                p["no_ask"] = None
                p["title"] = p["ticker"]
                p["close_time"] = ""
            enriched_positions.append(p)
    except Exception as e:
        enriched_positions = []

    # total_invested from positions is unreliable — Kalshi's get_positions() only returns
    # filled contract holdings, not resting limit orders. Use live balance instead:
    # remaining = balance (Kalshi already deducts resting order margin from balance)
    total_invested_positions = sum(max(p.get("cost", 0), 0) for p in enriched_positions)
    spent_or_committed = (config.BUDGET_CAP - balance) if balance is not None else None
    remaining_budget = balance if balance is not None else None

    recent_trades = db.get_recent_trades(limit=50)
    recent_cycles = db.get_recent_cycles(limit=10)

    return {
        "mode": "SANDBOX" if config.USE_SANDBOX else "LIVE",
        "budget_cap": config.BUDGET_CAP,
        "balance": balance,
        "balance_error": balance_error,
        "total_invested": spent_or_committed,   # money spent + resting orders
        "remaining_budget": remaining_budget,    # = live balance (most accurate)
        "positions": enriched_positions,
        "recent_trades": recent_trades,
        "recent_cycles": recent_cycles,
    }


if __name__ == "__main__":
    db.init_db()
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False)
