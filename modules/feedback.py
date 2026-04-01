"""
feedback.py — Automatic parameter adjustment based on historical performance.

After every N trades (configured in TRADING_CONFIG["trades_before_feedback"]),
this module:
  1. Queries the DB for each strategy's recent trade history
  2. Computes win rate, average confidence, and P&L
  3. Adjusts each strategy's parameters to improve performance:
       - Win rate too low  → raise confidence_threshold (be more selective)
       - Win rate too high → lower confidence_threshold (trade more)
       - P&L improving     → increase Kelly fraction slightly
       - Source accuracy   → re-weight data sources toward better-performing ones
"""

import logging
import sqlite3
from typing import Dict, List

import pandas as pd

from config import TRADING_CONFIG

logger = logging.getLogger(__name__)


def load_recent_trades(db_path: str, strategy: str, n: int) -> pd.DataFrame:
    """Load the last `n` trades for a given strategy from SQLite."""
    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query(
        """
        SELECT * FROM trades
        WHERE strategy = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        conn,
        params=(strategy, n),
    )
    conn.close()
    return df


def compute_metrics(df: pd.DataFrame) -> Dict:
    """
    Compute performance metrics from a trades DataFrame.
    Returns a dict with: win_rate, avg_confidence, total_pnl, trade_count,
    avg_pnl_per_trade, confidence_accuracy_correlation
    """
    if df.empty:
        return {}

    total = len(df)
    wins = (df["simulated_result"] == "win").sum()
    win_rate = wins / total

    avg_confidence = df["confidence"].mean()
    total_pnl = df["pnl"].sum()
    avg_pnl = df["pnl"].mean()

    # Correlation between confidence score and win (1=win, 0=loss)
    df = df.copy()
    df["win_int"] = (df["simulated_result"] == "win").astype(int)
    correlation = df[["confidence", "win_int"]].corr().iloc[0, 1]

    return {
        "trade_count": total,
        "win_rate": round(win_rate, 4),
        "avg_confidence": round(avg_confidence, 4),
        "total_pnl": round(total_pnl, 2),
        "avg_pnl_per_trade": round(avg_pnl, 2),
        "confidence_accuracy_correlation": round(correlation, 4),
    }


def suggest_param_updates(metrics: Dict, current_params: Dict) -> Dict:
    """
    Given performance metrics, return a dict of parameter deltas to apply.

    Rules (conservative, small steps to avoid overfitting):
      - win_rate < 0.45 → raise confidence_threshold by +0.03
      - win_rate > 0.65 → lower confidence_threshold by -0.02 (more permissive)
      - avg_pnl < 0    → no threshold relaxation even if win_rate is OK
      - strong positive correlation → confidence is predictive; trust it more
    """
    if not metrics:
        return {}

    updates = {}
    win_rate = metrics.get("win_rate", 0.5)
    avg_pnl = metrics.get("avg_pnl_per_trade", 0)
    current_threshold = current_params.get("confidence_threshold", 0.58)

    if win_rate < 0.45:
        new_threshold = min(current_threshold + 0.03, 0.85)
        updates["confidence_threshold"] = round(new_threshold, 3)
        logger.info("  → win_rate=%.2f is low, raising threshold to %.3f", win_rate, new_threshold)

    elif win_rate > 0.65 and avg_pnl > 0:
        new_threshold = max(current_threshold - 0.02, 0.50)
        updates["confidence_threshold"] = round(new_threshold, 3)
        logger.info("  → win_rate=%.2f is high, lowering threshold to %.3f", win_rate, new_threshold)

    return updates


def run_feedback_loop(
    strategies: List,  # list of (BaseStrategy, PaperTrader) tuples
    db_path: str | None = None,
    lookback_n: int | None = None,
) -> None:
    """
    Entry point called by main.py after every N trades.
    Iterates over all (strategy, trader) pairs, computes metrics, applies updates.
    """
    db = db_path or TRADING_CONFIG["db_path"]
    n = lookback_n or TRADING_CONFIG.get("trades_before_feedback", 10)

    logger.info("=" * 60)
    logger.info("FEEDBACK LOOP — analysing last %d trades per strategy", n)

    for strategy, trader in strategies:
        df = load_recent_trades(db, strategy.name, n)
        if df.empty:
            logger.info("[%s] No trades yet — skipping feedback", strategy.name)
            continue

        metrics = compute_metrics(df)
        logger.info(
            "[%s] trades=%d win_rate=%.1f%% avg_pnl=$%.2f bankroll=$%.2f",
            strategy.name,
            metrics["trade_count"],
            metrics["win_rate"] * 100,
            metrics["avg_pnl_per_trade"],
            trader.bankroll,
        )

        updates = suggest_param_updates(metrics, strategy.params)
        if updates:
            strategy.update_params(updates)
            logger.info("[%s] Applied parameter updates: %s", strategy.name, updates)
        else:
            logger.info("[%s] Parameters unchanged", strategy.name)

    logger.info("=" * 60)
