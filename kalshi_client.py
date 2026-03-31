import kalshi_python
import uuid
import time
import config

# Trending series to scan — simple binary yes/no price prediction markets
# KXNBAGAME is first so NBA game markets are always included before the limit is hit.
TRENDING_SERIES = [
    "KXNBAGAME", # NBA game winners (tonight's games, outcome known same day)
    "KXBTCD",    # Bitcoin daily price
    "KXBTCW",    # Bitcoin weekly price
    "KXETHD",    # Ethereum daily price
    "INXD",      # S&P 500 daily
    "KXNASDAQD", # Nasdaq daily
    "KXTRUMP",   # Trump-related political
    "KXFED",     # Fed rate decisions
    "KXCONGRESS",
    "KXDOGE",    # Dogecoin price
    "KXSOLD",    # Solana daily
]

# Series that are sports games — their markets close weeks later for settlement
# but the outcome is decided the same night, so skip the 24h filter for them.
SPORTS_GAME_SERIES = {"KXNBAGAME"}

# Min/max yes_ask to include in market list (20-80¢ = both sides ≥ 20¢)
MIN_PRICE = 20
MAX_PRICE = 80


def _make_client():
    cfg = kalshi_python.Configuration(host=config.KALSHI_HOST)
    api_client = kalshi_python.ApiClient(cfg)
    api_client.set_kalshi_auth(config.KALSHI_API_KEY_ID, config.KALSHI_PRIVATE_KEY_PATH)
    return api_client


def get_open_markets(limit=50):
    """Return simple yes/no binary markets from trending series, filtered to tradeable prices.

    Only returns markets that:
    - Close within 24 hours (so bets settle quickly) — filtered locally
    - Have YES ask in 20-80¢ range (both sides ≥ 20% probability)
    """
    import datetime as dt
    markets = []
    seen = set()
    cutoff = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=24)

    with _make_client() as api_client:
        events_api = kalshi_python.EventsApi(api_client)

        for series in TRENDING_SERIES:
            try:
                resp = events_api.get_events(
                    limit=10,
                    status="open",
                    series_ticker=series,
                    with_nested_markets=True,
                )
                is_sports = series in SPORTS_GAME_SERIES
                for event in (resp.events or []):
                    for m in (getattr(event, "markets", None) or []):
                        ticker = m.ticker or ""
                        if ticker in seen:
                            continue
                        seen.add(ticker)

                        # For non-sports: must close within 24 hours.
                        # For sports games: skip the 24h filter — game outcome is
                        # decided tonight even though settlement is confirmed later.
                        if not is_sports:
                            close_time = getattr(m, "close_time", None)
                            if close_time is None:
                                continue
                            if hasattr(close_time, "tzinfo") and close_time.tzinfo is not None:
                                if close_time > cutoff:
                                    continue
                            else:
                                if close_time > cutoff.replace(tzinfo=None):
                                    continue

                        yes = getattr(m, "yes_ask", None)
                        no = getattr(m, "no_ask", None)
                        if yes is None or no is None:
                            continue

                        # Only include markets where BOTH sides are ≥ 20¢
                        if yes < MIN_PRICE or yes > MAX_PRICE:
                            continue

                        markets.append({
                            "ticker": ticker,
                            "title": getattr(m, "title", "") or "",
                            "yes_ask": yes,
                            "no_ask": no,
                            "volume": getattr(m, "volume", 0),
                            "close_time": str(close_time)[:19],
                        })

                        if len(markets) >= limit:
                            return markets
            except Exception:
                continue

    return markets


def get_market(ticker):
    """Return detailed info for a single market."""
    with _make_client() as api_client:
        api = kalshi_python.MarketsApi(api_client)
        resp = api.get_market(ticker=ticker)
    m = resp.market
    return {
        "ticker": m.ticker,
        "title": getattr(m, "title", ""),
        "yes_ask": getattr(m, "yes_ask", None),
        "no_ask": getattr(m, "no_ask", None),
        "volume": getattr(m, "volume", 0),
        "close_time": str(getattr(m, "close_time", "")),
        "status": getattr(m, "status", ""),
        "result": getattr(m, "result", None),
    }


def get_balance():
    """Return available cash balance in dollars."""
    with _make_client() as api_client:
        api = kalshi_python.PortfolioApi(api_client)
        resp = api.get_balance()
    return (resp.balance or 0) / 100.0


def get_positions():
    """Return list of open positions."""
    with _make_client() as api_client:
        api = kalshi_python.PortfolioApi(api_client)
        resp = api.get_positions()
    positions = []
    for p in (resp.positions or []):
        positions.append({
            "ticker": p.ticker,
            "position": getattr(p, "position", 0),
            "cost": getattr(p, "market_exposure", 0) / 100.0,
            "total_traded": getattr(p, "total_traded", 0) / 100.0,
            "fees_paid": getattr(p, "fees_paid", 0) / 100.0,
            "realized_pnl": getattr(p, "realized_pnl", 0) / 100.0,
        })
    return positions


def place_order(ticker, side, count, yes_price_cents, action="buy"):
    """Place a limit order. Returns dict with order_id and status."""
    with _make_client() as api_client:
        api = kalshi_python.PortfolioApi(api_client)
        resp = api.create_order(
            ticker=ticker,
            action=action,
            type="limit",
            yes_price=yes_price_cents,
            count=count,
            client_order_id=str(uuid.uuid4()),
            side=side,
        )
    order = resp.order
    return {
        "order_id": order.order_id,
        "status": getattr(order, "status", ""),
        "ticker": ticker,
        "side": side,
        "count": count,
        "yes_price_cents": yes_price_cents,
    }


def get_open_orders():
    """Return all currently resting (unfilled) orders."""
    with _make_client() as api_client:
        api = kalshi_python.PortfolioApi(api_client)
        resp = api.get_orders(status="resting")
    orders = []
    for o in (resp.orders or []):
        price = getattr(o, "no_price", None) if o.side == "no" else getattr(o, "yes_price", None)
        count = getattr(o, "remaining_count", 0) or 0
        orders.append({
            "order_id": o.order_id,
            "ticker": o.ticker,
            "side": o.side,
            "count": count,
            "price_cents": price,
            "cost": (price or 0) * count / 100.0,
            "status": o.status,
        })
    return orders


def cancel_order(order_id):
    """Cancel an open order."""
    with _make_client() as api_client:
        api = kalshi_python.PortfolioApi(api_client)
        api.cancel_order(order_id=order_id)
    return {"cancelled": True, "order_id": order_id}
