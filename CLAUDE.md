# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the project

```bash
# Start the web dashboard (port 8000)
python dashboard.py

# Start the autonomous trading agent loop (runs every 30 min)
python main.py

# Run a single trading cycle manually
python -c "import db; db.init_db(); import agent; result = agent.run_cycle(); print(result)"

# Check current balance and positions
python -c "import kalshi_client; print(kalshi_client.get_balance()); print(kalshi_client.get_positions())"
```

## Architecture

The system has two independently running processes:

- **`main.py`** — infinite loop that calls `agent.run_cycle()` every `CYCLE_INTERVAL_MINUTES` minutes
- **`dashboard.py`** — FastAPI server that serves `templates/index.html` and exposes `/api/state`

The agent cycle flow inside `agent.run_cycle()`:
1. Claude (`claude-sonnet-4-6`) receives tools and a trading system prompt
2. Claude calls tools in a loop: `get_portfolio` → `get_open_markets` → `search_news` → `get_market_detail` → `place_trade` / `close_position` → `stop_trading`
3. Tool results are pruned from message history after each turn to stay under the 30k TPM rate limit
4. Every trade and cycle is logged to `trading.db` (SQLite)

## Key constraints and quirks

**Kalshi API:**
- Production host: `https://api.elections.kalshi.com/trade-api/v2`
- Auth uses RSA-PSS signing via `api_client.set_kalshi_auth(key_id, pem_path)` — NOT config properties
- `PortfolioApi.create_order()` takes kwargs directly (not a wrapped `CreateOrderRequest` object)
- `GetPositionsResponse` has `.positions` (not `.market_positions`)
- Balance is returned in **cents** — divide by 100 for dollars

**Market sourcing:**
- `kalshi_client.get_open_markets()` queries specific trending series via `EventsApi` (not `get_markets`) because the default `get_markets` endpoint returns only sports parlay combos
- Trending series queried: `KXBTCD`, `KXBTCW`, `KXETHD`, `INXD`, `KXNASDAQD`, `KXTRUMP`, `KXFED`, `KXCONGRESS`, `KXDOGE`, `KXSOLD`
- Pre-filtered to only return markets where `20 ≤ yes_ask ≤ 80` (both sides ≥ 20¢ probability)
- Pre-filtered to only return markets closing within 24 hours (`max_close_ts = now + 86400`)

**Trading rules (enforced in both Python and agent prompt):**
- `BUDGET_CAP` = $50 total — never exceed
- `MAX_SINGLE_TRADE` = $5 — max per market per trade
- **Minimum 20% win probability**: NEVER bet YES if `yes_ask < 20¢`; NEVER bet NO if `no_ask < 20¢` (i.e., `yes_ask > 80¢`)
- No parlays — only simple binary yes/no markets

**Token limits:**
- Claude Sonnet 4.6 has a 30k input tokens/min limit
- Markets are formatted as compact text tables (not JSON) to reduce tokens
- News searches are hard-capped at 6 per cycle, snippets truncated to 300 chars
- Old tool results in message history are pruned to `"[omitted to save context]"` after each turn

## Config (`.env`)

```
KALSHI_API_KEY_ID=...
KALSHI_PRIVATE_KEY_PATH=./kalshi_private_key.pem
ANTHROPIC_API_KEY=...
TAVILY_API_KEY=...
USE_SANDBOX=false          # true = demo-api.kalshi.co, false = api.elections.kalshi.com
BUDGET_CAP=50.0
MAX_SINGLE_TRADE=5.0
CYCLE_INTERVAL_MINUTES=30
```
