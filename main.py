"""
Kalshi autonomous trading agent — main loop.

Run this in a terminal to start the agent. It will execute a trading cycle
every CYCLE_INTERVAL_MINUTES minutes.

Usage:
    python main.py
"""

import os
import sys
import time
import traceback
import db
import agent
import kalshi_client
import config

_LOCK_FILE = "/tmp/kalshi_trading.lock"


def _acquire_lock():
    """Ensure only one instance of main.py runs at a time."""
    if os.path.exists(_LOCK_FILE):
        with open(_LOCK_FILE) as f:
            old_pid = f.read().strip()
        try:
            os.kill(int(old_pid), 0)  # check if process is alive
            print(f"[ERROR] Another main.py instance is already running (PID {old_pid}). Exiting.")
            sys.exit(1)
        except (ProcessLookupError, ValueError):
            pass  # stale lock — proceed
    with open(_LOCK_FILE, "w") as f:
        f.write(str(os.getpid()))


def _release_lock():
    try:
        os.remove(_LOCK_FILE)
    except FileNotFoundError:
        pass


def _check_dashboard_consistency(cycle_result):
    """
    After each cycle, compare the dashboard /api/state with the DB to flag
    any inconsistencies. Prints a short consistency report.
    """
    print("\n--- Dashboard consistency check ---")
    try:
        # Live Kalshi state
        live_balance = kalshi_client.get_balance()
        live_positions = kalshi_client.get_positions()

        # DB state
        db_trades = db.get_recent_trades(limit=100)
        db_cycles = db.get_recent_cycles(limit=1)

        # How much have we spent/committed (based on balance drop from BUDGET_CAP)
        spent_or_committed = config.BUDGET_CAP - live_balance
        db_buy_total = sum(
            t["total_cost"] for t in db_trades if t["action"] == "buy"
        )
        db_sell_total = sum(
            t["total_cost"] for t in db_trades if t["action"] == "sell"
        )
        db_net_cost = db_buy_total - db_sell_total

        last_cycle = db_cycles[0] if db_cycles else {}
        issues = []

        # Check 1: DB trade count in last cycle vs cycle result
        if cycle_result and cycle_result["trades_made"] != last_cycle.get("trades_made", -1):
            issues.append(
                f"Cycle trades_made mismatch: cycle_result={cycle_result['trades_made']} "
                f"db={last_cycle.get('trades_made')}"
            )

        # Check 2: Live balance in cycle log vs actual
        logged_balance = last_cycle.get("balance_after")
        if logged_balance is not None and abs(logged_balance - live_balance) > 0.10:
            issues.append(
                f"Balance drift: DB logged=${logged_balance:.2f}, live=${live_balance:.2f} "
                f"(diff=${abs(logged_balance - live_balance):.2f})"
            )

        # Check 3: Net DB cost vs balance drop
        if abs(db_net_cost - spent_or_committed) > 1.00:
            issues.append(
                f"Spend tracking gap: DB net cost=${db_net_cost:.2f}, "
                f"balance drop=${spent_or_committed:.2f} "
                f"(diff=${abs(db_net_cost - spent_or_committed):.2f} — likely resting orders)"
            )

        print(f"  Live balance:         ${live_balance:.2f}")
        print(f"  Spent/committed:      ${spent_or_committed:.2f}  (BUDGET_CAP - balance)")
        print(f"  DB net buy cost:      ${db_net_cost:.2f}  ({len(db_trades)} total trade rows)")
        print(f"  Open positions (API): {len(live_positions)}")

        if issues:
            print("  INCONSISTENCIES:")
            for issue in issues:
                print(f"    ⚠ {issue}")
        else:
            print("  ✓ Dashboard and DB are consistent")

    except Exception as e:
        print(f"  [consistency check error] {e}")

    print("-----------------------------------")


def main():
    print("=" * 60)
    print("Kalshi Autonomous Trading Agent")
    print(f"  Mode:       {'SANDBOX (demo)' if config.USE_SANDBOX else '*** LIVE PRODUCTION ***'}")
    print(f"  Budget cap: ${config.BUDGET_CAP:.2f}")
    print(f"  Max trade:  ${config.MAX_SINGLE_TRADE:.2f}")
    print(f"  Interval:   {config.CYCLE_INTERVAL_MINUTES} minutes")
    print("=" * 60)
    print()

    _acquire_lock()
    import atexit
    atexit.register(_release_lock)

    db.init_db()

    cycle_number = 0

    while True:
        print(f"\nSleeping {config.CYCLE_INTERVAL_MINUTES} minutes until next cycle...")
        time.sleep(config.CYCLE_INTERVAL_MINUTES * 60)

        cycle_number += 1
        print(f"\n{'=' * 40}")
        print(f"Starting cycle #{cycle_number}")
        print(f"{'=' * 40}")

        result = None
        try:
            result = agent.run_cycle()
            print(f"\nCycle #{cycle_number} complete:")
            print(f"  Markets scanned: {result['markets_scanned']}")
            print(f"  Trades made:     {result['trades_made']}")
            print(f"  Balance:         ${result['balance']:.2f}" if result["balance"] is not None else "  Balance:         N/A")
            print(f"  Summary: {result['summary'][:200]}")
        except Exception:
            print(f"\n[ERROR] Cycle #{cycle_number} failed:")
            traceback.print_exc()

        _check_dashboard_consistency(result)


if __name__ == "__main__":
    main()
