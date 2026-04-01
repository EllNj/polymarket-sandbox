"""
position_manager.py — Early exit logic for open paper trade positions.

Instead of always waiting for full market resolution (days/weeks), this
module checks if current market odds have moved enough to justify closing
a position early — either to lock in profit or cut losses.

How it works:
  - Each cycle, fetches current odds for all markets with pending trades
  - Take profit: if odds moved 25%+ in your favour → sell, bank the gain
  - Stop loss:   if odds moved 40%+ against you  → sell, limit the damage

P&L formula (prediction market shares):
  Shares bought = bet_size / entry_odds
  Current value = shares * current_odds
  P&L = current_value - bet_size

Example:
  Bet $50 on Yes at 0.40 → 125 shares
  Odds now 0.65 → value = 125 * 0.65 = $81.25 → P&L = +$31.25
  Odds now 0.22 → value = 125 * 0.22 = $27.50 → P&L = -$22.50
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
# THRESHOLDS — edit these to tune aggressiveness
# ---------------------------------------------------------------------------
TAKE_PROFIT = TRADING_CONFIG.get("take_profit_threshold", 0.25)  # 25% odds move in favour
STOP_LOSS   = TRADING_CONFIG.get("stop_loss_threshold",   0.40)  # 40% odds move against


# ---------------------------------------------------------------------------
# FETCH CURRENT ODDS
# ---------------------------------------------------------------------------

def _fetch_current_odds(market_id: str) -> Optional[Dict[str, float]]:
    """
    Fetch current outcome prices for a market from the Gamma API.
    Returns dict like {"Yes": 0.65, "No": 0.35} or None on error.
    """
    try:
        resp = requests.get(
            f"{GAMMA_API}/markets/{market_id}",
            timeout=10,
            headers={"User-Agent": "polymarket-sandbox-bot/1.0"},
        )
        resp.raise_for_status()
        data = resp.json()

        import json  # noqa: PLC0415
        outcomes_raw = data.get("outcomes", "[]")
        prices_raw   = data.get("outcomePrices", "[]")

        if isinstance(outcomes_raw, str):
            outcomes_raw = json.loads(outcomes_raw)
        if isinstance(prices_raw, str):
            prices_raw = json.loads(prices_raw)

        return {
            str(o): round(float(p), 4)
            for o, p in zip(outcomes_raw, prices_raw)
        }
    except Exception as exc:
        logger.debug("Could not fetch current odds for %s: %s", market_id, exc)
        return None


# ---------------------------------------------------------------------------
# EARLY EXIT CHECK
# ---------------------------------------------------------------------------

def _should_exit(entry_odds: float, current_odds: float, outcome_bet: str) -> tuple[bool, str]:
    """
    Decide whether to exit a position based on how much odds have moved.

    Returns (should_exit, reason_string).
    """
    if entry_odds <= 0 or current_odds <= 0:
        return False, ""

    # How much odds have changed relative to entry
    change = (current_odds - entry_odds) / entry_odds  # positive = moved in our favour

    if change >= TAKE_PROFIT:
        return True, f"take_profit (odds {entry_odds:.2f}→{current_odds:.2f}, +{change*100:.0f}%)"
    if change <= -STOP_LOSS:
        return True, f"stop_loss (odds {entry_odds:.2f}→{current_odds:.2f}, {change*100:.0f}%)"

    return False, ""


# ---------------------------------------------------------------------------
# MAIN ENTRY POINT
# ---------------------------------------------------------------------------

def check_early_exits(db_path: Optional[str] = None) -> int:
    """
    Check all pending trades for early exit conditions.
    Updates the database and returns the number of positions closed.
    """
    db = db_path or TRADING_CONFIG["db_path"]
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row

    pending = conn.execute(
        """
        SELECT id, market_id, strategy, outcome_bet, bet_size, market_odds, bankroll_after
        FROM trades
        WHERE status = 'pending'
        ORDER BY market_id
        """
    ).fetchall()

    if not pending:
        logger.debug("Position manager: no pending trades to check")
        conn.close()
        return 0

    # Group by market to minimise API calls
    markets_needed = list({row["market_id"] for row in pending})
    current_odds_map: Dict[str, Dict[str, float]] = {}

    for market_id in markets_needed:
        odds = _fetch_current_odds(market_id)
        if odds:
            current_odds_map[market_id] = odds

    closed = 0
    for row in pending:
        market_id   = row["market_id"]
        outcome_bet = row["outcome_bet"]
        entry_odds  = row["market_odds"]
        bet_size    = row["bet_size"]

        if market_id not in current_odds_map:
            continue

        current_odds = current_odds_map[market_id].get(outcome_bet)
        if current_odds is None:
            continue

        should_exit, reason = _should_exit(entry_odds, current_odds, outcome_bet)
        if not should_exit:
            continue

        # Calculate P&L: shares * current_price - cost
        shares = bet_size / max(entry_odds, 0.001)
        current_value = shares * current_odds
        pnl = round(current_value - bet_size, 2)

        # Update the trade record
        result = "win" if pnl > 0 else "loss"
        conn.execute(
            """
            UPDATE trades SET
                simulated_result = ?,
                pnl              = ?,
                status           = 'closed_early',
                resolved_outcome = ?,
                resolution_time  = ?,
                rationale        = rationale || ' | EARLY EXIT: ' || ?
            WHERE id = ?
            """,
            (result, pnl, outcome_bet, datetime.now(timezone.utc).isoformat(), reason, row["id"]),
        )

        closed += 1
        logger.info(
            "Early exit [%s] market=%s bet=%s entry=%.2f current=%.2f pnl=$%.2f — %s",
            row["strategy"], market_id, outcome_bet,
            entry_odds, current_odds, pnl, reason,
        )

    conn.commit()
    conn.close()

    if closed:
        logger.info("Position manager: closed %d positions early", closed)

    return closed
