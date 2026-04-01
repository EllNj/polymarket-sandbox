"""
resolver.py — Checks PolyMarket's Gamma API for real market resolutions
and updates pending trades in the database with actual outcomes.

How it works:
  1. Queries the DB for all trades with status='pending'
  2. Groups them by market_id
  3. Hits the Gamma API for each market to check if it has resolved
  4. If resolved, determines the winning outcome (price → 1.0, loser → 0.0)
  5. Updates each affected trade: sets real win/loss, real P&L, status='resolved'

The feedback loop and analytics ignore pending trades automatically —
only resolved trades count toward performance metrics.

Call run_resolver() from main.py on a schedule (e.g. every hour).
"""

import logging
import sqlite3
from datetime import datetime, timezone
from typing import Dict, List, Optional

import requests

from config import TRADING_CONFIG

logger = logging.getLogger(__name__)

GAMMA_API = "https://gamma-api.polymarket.com"


# ---------------------------------------------------------------------------
# GAMMA API — check if a market has resolved
# ---------------------------------------------------------------------------

def fetch_market_resolution(market_id: str) -> Optional[Dict]:
    """
    Fetch a market from the Gamma API and return resolution info if resolved.

    Returns dict with keys:
        resolved        – bool
        winning_outcome – str or None (e.g. "Yes" or "No")
        resolution_time – ISO timestamp string or None

    Returns None on API error.
    """
    try:
        resp = requests.get(
            f"{GAMMA_API}/markets/{market_id}",
            timeout=10,
            headers={"User-Agent": "polymarket-sandbox-bot/1.0"},
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        logger.warning("Gamma API error for market %s: %s", market_id, exc)
        return None

    resolved = data.get("resolved", False) or data.get("closed", False)
    if not resolved:
        return {"resolved": False, "winning_outcome": None, "resolution_time": None}

    # Determine winner from outcomePrices — resolved winner has price ~1.0
    outcomes_raw = data.get("outcomes", "[]")
    prices_raw = data.get("outcomePrices", "[]")

    import json  # noqa: PLC0415
    if isinstance(outcomes_raw, str):
        try:
            outcomes_raw = json.loads(outcomes_raw)
        except Exception:
            outcomes_raw = []
    if isinstance(prices_raw, str):
        try:
            prices_raw = json.loads(prices_raw)
        except Exception:
            prices_raw = []

    winning_outcome = None
    best_price = -1.0
    for outcome, price in zip(outcomes_raw, prices_raw):
        try:
            p = float(price)
            if p > best_price:
                best_price = p
                winning_outcome = str(outcome)
        except (ValueError, TypeError):
            continue

    # Only treat as definitively resolved if a clear winner exists (price > 0.9)
    if best_price < 0.9:
        logger.debug("Market %s closed but no clear winner yet (best_price=%.2f)", market_id, best_price)
        return {"resolved": False, "winning_outcome": None, "resolution_time": None}

    resolution_time = data.get("resolutionTime") or data.get("endDate") or datetime.now(timezone.utc).isoformat()

    logger.info("Market %s resolved → winner: %s", market_id, winning_outcome)
    return {
        "resolved": True,
        "winning_outcome": winning_outcome,
        "resolution_time": resolution_time,
    }


# ---------------------------------------------------------------------------
# DATABASE UPDATES
# ---------------------------------------------------------------------------

def get_pending_market_ids(db_path: str) -> List[str]:
    """Return distinct market_ids that have at least one pending trade."""
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT DISTINCT market_id FROM trades WHERE status = 'pending'"
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def update_trades_for_market(db_path: str, market_id: str, winning_outcome: str, resolution_time: str) -> int:
    """
    Update all pending trades for a market with real resolution data.
    Returns the number of trades updated.
    """
    conn = sqlite3.connect(db_path)

    pending = conn.execute(
        "SELECT id, outcome_bet, bet_size, market_odds FROM trades WHERE market_id = ? AND status = 'pending'",
        (market_id,),
    ).fetchall()

    updated = 0
    for row in pending:
        trade_id, outcome_bet, bet_size, market_odds = row

        won = (outcome_bet == winning_outcome)
        actual_result = "win" if won else "loss"

        if won:
            pnl = round(bet_size * ((1.0 / max(market_odds, 0.01)) - 1.0), 2)
        else:
            pnl = -bet_size

        conn.execute(
            """
            UPDATE trades SET
                simulated_result = ?,
                pnl              = ?,
                status           = 'resolved',
                resolved_outcome = ?,
                resolution_time  = ?
            WHERE id = ?
            """,
            (actual_result, pnl, winning_outcome, resolution_time, trade_id),
        )
        updated += 1

    # Recalculate bankroll_after for this strategy in chronological order
    # (since P&L values just changed for resolved trades)
    strategies_affected = conn.execute(
        "SELECT DISTINCT strategy FROM trades WHERE market_id = ?", (market_id,)
    ).fetchall()

    for (strategy,) in strategies_affected:
        _recalculate_bankroll(conn, strategy)

    conn.commit()
    conn.close()
    logger.info("Updated %d trades for market %s (winner=%s)", updated, market_id, winning_outcome)
    return updated


def _recalculate_bankroll(conn: sqlite3.Connection, strategy: str) -> None:
    """
    Recalculate bankroll_after for all resolved trades for a strategy,
    in chronological order. Pending trades keep their last known bankroll.
    """
    initial = TRADING_CONFIG["initial_bankroll"]
    rows = conn.execute(
        """
        SELECT id, pnl, status FROM trades
        WHERE strategy = ?
        ORDER BY id ASC
        """,
        (strategy,),
    ).fetchall()

    running = initial
    for trade_id, pnl, status in rows:
        if status == "resolved":
            running = round(running + pnl, 2)
            conn.execute(
                "UPDATE trades SET bankroll_after = ? WHERE id = ?",
                (running, trade_id),
            )
        # Pending trades: leave bankroll_after as-is (it's an estimate)


# ---------------------------------------------------------------------------
# MAIN ENTRY POINT
# ---------------------------------------------------------------------------

def run_resolver(db_path: Optional[str] = None) -> None:
    """
    Check all pending trades against the PolyMarket Gamma API.
    Call this periodically from main.py (e.g. every hour).
    """
    db = db_path or TRADING_CONFIG["db_path"]
    pending_markets = get_pending_market_ids(db)

    if not pending_markets:
        logger.info("Resolver: no pending trades to check")
        return

    logger.info("Resolver: checking %d markets with pending trades", len(pending_markets))
    resolved_count = 0

    for market_id in pending_markets:
        result = fetch_market_resolution(market_id)
        if result is None:
            continue  # API error — try again next cycle

        if result["resolved"]:
            n = update_trades_for_market(
                db,
                market_id,
                result["winning_outcome"],
                result["resolution_time"],
            )
            resolved_count += n
        else:
            logger.debug("Market %s still open/unresolved", market_id)

    logger.info("Resolver: resolved %d trades this cycle", resolved_count)
