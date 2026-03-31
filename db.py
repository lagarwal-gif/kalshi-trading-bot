import sqlite3
from datetime import datetime, timezone

DB_PATH = "trading.db"


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                ticker TEXT NOT NULL,
                market_title TEXT,
                side TEXT NOT NULL,
                action TEXT NOT NULL,
                count INTEGER NOT NULL,
                price_cents INTEGER NOT NULL,
                total_cost REAL NOT NULL,
                reasoning TEXT,
                order_id TEXT,
                status TEXT NOT NULL DEFAULT 'placed'
            );

            CREATE TABLE IF NOT EXISTS cycles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                markets_scanned INTEGER DEFAULT 0,
                trades_made INTEGER DEFAULT 0,
                balance_after REAL,
                total_invested REAL,
                summary TEXT
            );
        """)


def log_trade(ticker, market_title, side, action, count, price_cents, total_cost, reasoning, order_id, status="placed"):
    ts = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO trades
               (timestamp, ticker, market_title, side, action, count, price_cents, total_cost, reasoning, order_id, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (ts, ticker, market_title, side, action, count, price_cents, total_cost, reasoning, order_id, status),
        )
    return ts


def log_cycle(markets_scanned, trades_made, balance_after, total_invested, summary):
    ts = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO cycles
               (timestamp, markets_scanned, trades_made, balance_after, total_invested, summary)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (ts, markets_scanned, trades_made, balance_after, total_invested, summary),
        )


def get_recent_trades(limit=50):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM trades ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_recent_cycles(limit=20):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM cycles ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]
