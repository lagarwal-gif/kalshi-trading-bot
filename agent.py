"""
Claude trading agent.

Runs one full reasoning cycle: scans markets, reads news, decides which
bets to make, executes them, and returns a summary dict.
"""

import json
import time
import anthropic
import kalshi_client
import news_client
import db
import config

_anthropic = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

SYSTEM_PROMPT = """You are an autonomous prediction market trader operating on Kalshi.

MARKET STRUCTURE: All available markets are simple binary YES/NO questions — primarily crypto price levels (Bitcoin, Ethereum), stock index levels (S&P 500, Nasdaq), and macro/political events (Fed rate, Congress, Trump).
YES pays $1 if the event happens; NO pays $1 if it does not. Prices are in cents.

Your goal: find markets where one side is mispriced relative to the true probability.

Rules:
- Total budget $50. Never invest more than this.
- Max $5 on a single market.
- NEVER bet YES if the yes_ask is below 20¢ (implied win probability < 20%).
- NEVER bet NO if the no_ask is below 20¢ (i.e., yes_ask > 80¢).
- Check portfolio first (it shows both held_positions AND resting_orders). Do NOT place a new bet on a market where you already have a resting order.
- Then get markets, then search news (max 6 searches).
- Be decisive — after 4-5 searches, pick your best 2-3 trades and place them. Do not over-research.
- Prices in cents (1-99). YES at 30¢: costs $0.30/contract, pays $1 if YES resolves.
- Today is February 22, 2026. ONLY trade markets closing within 24 hours — all markets shown to you already meet this requirement.
- Size your bets by conviction: stronger edge → more contracts (up to $5 total per market).

When done, call stop_trading with a summary."""

TOOLS = [
    {
        "name": "search_news",
        "description": "Search for recent news and information on any topic. Use this to get current data before making trading decisions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query, e.g. 'Federal Reserve interest rate decision 2025' or 'Bitcoin price forecast'"
                },
                "max_results": {
                    "type": "integer",
                    "description": "Number of results to return (default 5)",
                    "default": 5
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "get_open_markets",
        "description": "Get a list of currently open Kalshi prediction markets. Returns ticker, title, yes/no prices, volume, and close time.",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Max number of markets to return (default 30, max 50)",
                    "default": 30
                }
            },
            "required": []
        }
    },
    {
        "name": "get_market_detail",
        "description": "Get detailed information and order book for a specific market by its ticker.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "The market ticker, e.g. 'FED-25DEC-T4.50'"
                }
            },
            "required": ["ticker"]
        }
    },
    {
        "name": "get_portfolio",
        "description": "Get your current cash balance and all open positions.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "place_trade",
        "description": "Place a trade on a Kalshi market. Buys contracts at the specified price.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "The market ticker"
                },
                "side": {
                    "type": "string",
                    "enum": ["yes", "no"],
                    "description": "Whether to buy YES or NO contracts"
                },
                "count": {
                    "type": "integer",
                    "description": "Number of contracts to buy"
                },
                "yes_price_cents": {
                    "type": "integer",
                    "description": "Limit price for YES contracts in cents (1-99). For NO contracts, the system will derive the correct price.",
                    "minimum": 1,
                    "maximum": 99
                },
                "reasoning": {
                    "type": "string",
                    "description": "Your reasoning for this trade (2-3 sentences). This is logged for the dashboard."
                },
                "market_title": {
                    "type": "string",
                    "description": "Human-readable title of the market being traded"
                }
            },
            "required": ["ticker", "side", "count", "yes_price_cents", "reasoning", "market_title"]
        }
    },
    {
        "name": "close_position",
        "description": "Sell all contracts in an existing position to exit it.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "The market ticker of the position to close"
                },
                "reasoning": {
                    "type": "string",
                    "description": "Why you are closing this position"
                }
            },
            "required": ["ticker", "reasoning"]
        }
    },
    {
        "name": "stop_trading",
        "description": "Signal that you are done trading for this cycle.",
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "A brief summary of what you did this cycle and your overall portfolio outlook."
                }
            },
            "required": ["summary"]
        }
    }
]


def _get_total_invested(positions):
    """Sum up total dollars currently invested across open positions."""
    return sum(max(p.get("cost", 0), 0) for p in positions)


def _handle_tool(name, inputs, state):
    """
    Execute a tool call and return (result_str, done).
    state: dict with 'trades_made', 'markets_scanned', 'summary', 'positions'
    """
    try:
        if name == "search_news":
            if state["news_searches"] >= 6:
                return "News search limit reached for this cycle. Please make your trading decisions now based on what you know.", False
            state["news_searches"] += 1
            results = news_client.search(inputs["query"], max_results=3)
            if not results:
                return "No search results found.", False
            lines = []
            for r in results:
                snippet = r.get("content", "").strip()[:300]
                lines.append(f"[{r.get('url', '')}]\n{snippet}")
            return "\n\n".join(lines), False

        elif name == "get_open_markets":
            limit = min(inputs.get("limit", 30), 50)
            markets = kalshi_client.get_open_markets(limit=limit)
            state["markets_scanned"] = len(markets)
            # Compact text table — far fewer tokens than JSON
            rows = [f"{'TICKER':<30} {'YES':>4} {'NO':>4} {'VOL':>6}  CLOSES"]
            rows.append("-" * 65)
            for m in markets:
                yes = m.get("yes_ask") or 0
                no  = m.get("no_ask")  or 0
                vol = m.get("volume")  or 0
                closes = (m.get("close_time") or "")[:16]
                title = m.get("title", "")[:28]
                rows.append(f"{m['ticker']:<30} {yes:>4} {no:>4} {vol:>6}  {closes}  {title}")
            return "\n".join(rows), False

        elif name == "get_market_detail":
            ticker = inputs["ticker"]
            detail = kalshi_client.get_market(ticker)
            # No orderbook — Claude only needs current price to decide
            return (
                f"Ticker: {detail.get('ticker')}\n"
                f"Title: {detail.get('title')}\n"
                f"YES ask: {detail.get('yes_ask')}¢  NO ask: {detail.get('no_ask')}¢\n"
                f"Volume: {detail.get('volume')}  Closes: {str(detail.get('close_time', ''))[:16]}\n"
                f"Status: {detail.get('status')}  Result: {detail.get('result')}"
            ), False

        elif name == "get_portfolio":
            balance = kalshi_client.get_balance()
            positions = kalshi_client.get_positions()
            open_orders = kalshi_client.get_open_orders()
            state["positions"] = positions
            # balance already accounts for resting orders — it's the true available cash.
            # Resting orders tie up cash even though they don't appear as "positions".
            resting_cost = sum(o.get("cost", 0) for o in open_orders)
            result = {
                "balance_dollars": balance,
                "available_to_trade_dollars": balance,
                "budget_cap_dollars": config.BUDGET_CAP,
                "note": "balance is true available cash — Kalshi deducts resting orders from it automatically",
                "held_positions": positions,
                "resting_orders": open_orders,
                "resting_orders_cost": resting_cost,
            }
            return json.dumps(result, default=str), False

        elif name == "place_trade":
            ticker = inputs["ticker"]
            side = inputs["side"]
            count = inputs["count"]
            yes_price_cents = inputs["yes_price_cents"]
            reasoning = inputs["reasoning"]
            market_title = inputs.get("market_title", ticker)

            # Calculate cost in dollars
            if side == "yes":
                cost_dollars = (yes_price_cents / 100.0) * count
            else:
                no_price_cents = 100 - yes_price_cents
                cost_dollars = (no_price_cents / 100.0) * count

            # Minimum 20% win probability check
            if side == "yes" and yes_price_cents < 20:
                return f"BLOCKED: YES price {yes_price_cents}¢ implies <20% win probability. Minimum allowed is 20¢.", False
            if side == "no" and (100 - yes_price_cents) < 20:
                return f"BLOCKED: NO price {100 - yes_price_cents}¢ implies <20% win probability (yes_ask={yes_price_cents}¢). Minimum allowed is 20¢.", False

            if cost_dollars > config.MAX_SINGLE_TRADE:
                return f"BLOCKED: Trade cost ${cost_dollars:.2f} exceeds per-trade limit of ${config.MAX_SINGLE_TRADE:.2f}. Reduce count or price.", False

            # Use live balance as budget authority — Kalshi deducts resting orders from balance,
            # so this is always accurate even when positions API returns empty.
            live_balance = kalshi_client.get_balance()
            if cost_dollars > live_balance:
                return f"BLOCKED: Insufficient balance. Cost ${cost_dollars:.2f} > available balance ${live_balance:.2f}.", False

            # Hard cap: never let total spending exceed BUDGET_CAP
            spent_so_far = config.BUDGET_CAP - live_balance
            if spent_so_far + cost_dollars > config.BUDGET_CAP:
                remaining = config.BUDGET_CAP - spent_so_far
                return f"BLOCKED: Would exceed ${config.BUDGET_CAP:.0f} budget cap. Already spent/committed: ${spent_so_far:.2f}. Remaining: ${remaining:.2f}.", False

            positions = kalshi_client.get_positions()
            state["positions"] = positions

            # Execute the order
            result = kalshi_client.place_order(
                ticker=ticker,
                side=side,
                count=count,
                yes_price_cents=yes_price_cents,
                action="buy",
            )

            # Log to DB
            db.log_trade(
                ticker=ticker,
                market_title=market_title,
                side=side,
                action="buy",
                count=count,
                price_cents=yes_price_cents if side == "yes" else (100 - yes_price_cents),
                total_cost=cost_dollars,
                reasoning=reasoning,
                order_id=result.get("order_id", ""),
                status=result.get("status", "placed"),
            )

            state["trades_made"] += 1
            return f"Order placed: {count} {side.upper()} contracts on {ticker} at {yes_price_cents}¢. Order ID: {result.get('order_id')}. Cost: ${cost_dollars:.2f}", False

        elif name == "close_position":
            ticker = inputs["ticker"]
            reasoning = inputs["reasoning"]

            positions = kalshi_client.get_positions()
            pos = next((p for p in positions if p["ticker"] == ticker), None)

            if not pos or pos["position"] == 0:
                return f"No open position found for {ticker}.", False

            net = pos["position"]
            side = "yes" if net > 0 else "no"
            count = abs(net)

            # To close: sell what you hold
            result = kalshi_client.place_order(
                ticker=ticker,
                side=side,
                count=count,
                yes_price_cents=1,  # market sell — set low price to fill immediately
                action="sell",
            )

            db.log_trade(
                ticker=ticker,
                market_title=ticker,
                side=side,
                action="sell",
                count=count,
                price_cents=1,
                total_cost=0,
                reasoning=reasoning,
                order_id=result.get("order_id", ""),
                status="placed",
            )

            state["trades_made"] += 1
            return f"Close order placed for {count} {side.upper()} contracts on {ticker}.", False

        elif name == "stop_trading":
            state["summary"] = inputs.get("summary", "")
            return "Trading cycle complete.", True

        else:
            return f"Unknown tool: {name}", False

    except Exception as e:
        return f"Error executing {name}: {e}", False


def run_cycle():
    """
    Run one full agent cycle. Returns a summary dict.
    """
    state = {
        "trades_made": 0,
        "markets_scanned": 0,
        "summary": "",
        "positions": [],
        "news_searches": 0,
    }

    messages = [
        {
            "role": "user",
            "content": "Start your trading cycle. First check your portfolio, then scan markets and news, then make your best trades. When done, call stop_trading."
        }
    ]

    print("[agent] Starting trading cycle...")
    max_turns = 30

    for turn in range(max_turns):
        for attempt in range(3):
            try:
                response = _anthropic.messages.create(
                    model="claude-sonnet-4-6",
                    max_tokens=4096,
                    system=SYSTEM_PROMPT,
                    tools=TOOLS,
                    messages=messages,
                )
                break
            except anthropic.RateLimitError:
                wait = 30 * (attempt + 1)
                print(f"  [rate limit] waiting {wait}s...")
                time.sleep(wait)
        else:
            print("[agent] Rate limit retries exhausted, ending cycle.")
            break

        # Append assistant response to message history
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            print("[agent] Agent ended turn without calling stop_trading.")
            break

        if response.stop_reason != "tool_use":
            break

        # Process all tool calls in this response
        tool_results = []
        done = False

        for block in response.content:
            if block.type != "tool_use":
                continue

            tool_name = block.name
            tool_inputs = block.input
            print(f"  [tool] {tool_name}({json.dumps(tool_inputs, default=str)[:120]})")

            result_str, is_done = _handle_tool(tool_name, tool_inputs, state)
            if is_done:
                done = True

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result_str,
            })

        messages.append({"role": "user", "content": tool_results})

        # Prune large tool results from earlier turns to keep context lean.
        # Keep only the most recent tool_result turn verbose; collapse older ones.
        _LARGE_TOOLS = {"get_open_markets", "search_news"}
        if len(messages) > 4:
            for old_msg in messages[:-2]:  # everything except the last assistant+user pair
                if old_msg["role"] == "user" and isinstance(old_msg["content"], list):
                    for item in old_msg["content"]:
                        if item.get("type") == "tool_result":
                            # Identify which tool this result belongs to by scanning assistant msgs
                            content_str = item.get("content", "")
                            if (
                                isinstance(content_str, str)
                                and len(content_str) > 400
                                and not content_str.startswith("[omitted]")
                            ):
                                item["content"] = "[omitted to save context]"

        if done:
            break

    # Get final balance for logging
    try:
        final_balance = kalshi_client.get_balance()
        positions = kalshi_client.get_positions()
        total_invested = _get_total_invested(positions)
    except Exception as e:
        print(f"[agent] Warning: could not fetch final balance: {e}")
        final_balance = None
        total_invested = None

    db.log_cycle(
        markets_scanned=state["markets_scanned"],
        trades_made=state["trades_made"],
        balance_after=final_balance,
        total_invested=total_invested,
        summary=state["summary"],
    )

    print(f"[agent] Cycle done. Trades made: {state['trades_made']}. Balance: ${final_balance}")

    return {
        "trades_made": state["trades_made"],
        "markets_scanned": state["markets_scanned"],
        "balance": final_balance,
        "total_invested": total_invested,
        "summary": state["summary"],
    }
