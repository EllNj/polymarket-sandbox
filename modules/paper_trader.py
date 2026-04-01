"""
paper_trader.py — Simulated trading engine + SQLite trade log.

Responsibilities:
  1. Calculate bet size using fractional Kelly criterion
  2. Log trades as 'pending' — real outcomes come from resolver.py
  3. Track estimated bankroll per strategy (corrected when trades resolve)

Schema (trades table):
  id, timestamp, market_id, market_question, strategy, outcome_bet,
  confidence, bet_size, market_odds, simulated_result, pnl,
  bankroll_after, rationale, raw_sentiment, status,
  resolved_outcome, resolution_time
"""

import logging
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Dict

from config import TRADING_CONFIG
from modules.strategies.base import TradeSignal

logger = logging.getLogger(__name__)

_local = threading.local()


def _get_conn(db_path: str) -> sqlite3.Connection:
    """Return a per-thread SQLite connection, creating it if needed."""
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(db_path, check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row
    return _local.conn


def init_db(db_path: str) -> None:
    """
    Create the trades table if it doesn't exist.
    Also runs migrations to add new columns to existing databases.
    Safe to call multiple times.
    """
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp        TEXT    NOT NULL,
            market_id        TEXT    NOT NULL,
            market_question  TEXT    NOT NULL,
            strategy         TEXT    NOT NULL,
            outcome_bet      TEXT    NOT NULL,
            confidence       REAL    NOT NULL,
            bet_size         REAL    NOT NULL,
            market_odds      REAL    NOT NULL,
            simulated_result TEXT    NOT NULL DEFAULT 'pending',
            pnl              REAL    NOT NULL DEFAULT 0.0,
            bankroll_after   REAL    NOT NULL,
            rationale        TEXT,
            raw_sentiment    REAL,
            status           TEXT    NOT NULL DEFAULT 'pending',
            resolved_outcome TEXT,
            resolution_time  TEXT
        )
    """)

    # Migrate existing databases that don't have the new columns
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(trades)").fetchall()}
    migrations = {
        "status":           "ALTER TABLE trades ADD COLUMN status TEXT NOT NULL DEFAULT 'pending'",
        "resolved_outcome": "ALTER TABLE trades ADD COLUMN resolved_outcome TEXT",
        "resolution_time":  "ALTER TABLE trades ADD COLUMN resolution_time TEXT",
        "market_url":       "ALTER TABLE trades ADD COLUMN market_url TEXT DEFAULT ''",
    }
    for col, sql in migrations.items():
        if col not in existing_cols:
            conn.execute(sql)
            logger.info("DB migration: added column '%s'", col)

    conn.commit()
    conn.close()
    logger.info("Database initialised at %s", db_path)


# ---------------------------------------------------------------------------
# KELLY CRITERION BET SIZING
# ---------------------------------------------------------------------------

def _kelly_bet(bankroll: float, confidence: float, market_odds: float) -> float:
    """
    Fractional Kelly bet sizing.

    Kelly formula: f* = (p*b - q) / b
      p = estimated win probability (confidence)
      q = 1 - p
      b = net odds (payout - 1)

    We use quarter-Kelly (kelly_fraction=0.25) for safety.
    Result is capped at max_bet_fraction of bankroll.
    """
    kelly_fraction = TRADING_CONFIG.get("kelly_fraction", 0.25)
    max_fraction = TRADING_CONFIG.get("max_bet_fraction", 0.05)

    p = confidence
    q = 1.0 - p
    b = (1.0 / max(market_odds, 0.01)) - 1.0

    if b <= 0:
        return 0.0

    raw_kelly = (p * b - q) / b
    fractional_kelly = raw_kelly * kelly_fraction
    fraction = max(0.0, min(fractional_kelly, max_fraction))
    return round(bankroll * fraction, 2)


# ---------------------------------------------------------------------------
# PAPER TRADER CLASS
# ---------------------------------------------------------------------------

class PaperTrader:
    """
    One PaperTrader instance per strategy.
    Logs trades as 'pending' — resolver.py updates them with real outcomes.
    Tracks an estimated bankroll (corrected when markets resolve).
    """

    def __init__(self, strategy_name: str, db_path: str | None = None):
        self.strategy_name = strategy_name
        self.db_path = db_path or TRADING_CONFIG["db_path"]
        self.bankroll = TRADING_CONFIG["initial_bankroll"]
        self.trade_count = 0
        self._lock = threading.Lock()
        self.logger = logging.getLogger(f"trader.{strategy_name}")

    def _already_traded(self, market_id: str, hours: int = 20) -> bool:
        """Return True if we already have a pending trade on this market within the last N hours."""
        conn = _get_conn(self.db_path)
        from datetime import datetime, timezone, timedelta  # noqa: PLC0415
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        row = conn.execute(
            "SELECT id FROM trades WHERE strategy=? AND market_id=? AND timestamp>? AND status='pending' LIMIT 1",
            (self.strategy_name, market_id, since),
        ).fetchone()
        return row is not None

    def execute(self, market: Dict, signal: TradeSignal) -> Dict | None:
        """
        Evaluate the signal, size the bet, log as pending.
        Real outcome will be filled in by resolver.py when the market settles.
        Returns the trade record dict, or None if the trade was skipped.
        """
        if self._already_traded(market["id"]):
            self.logger.debug("Already have a pending trade on %s — skipping", market["id"])
            return None

        if not signal.should_trade:
            self.logger.debug("Signal says skip — no trade for %s", market["id"])
            return None

        min_confidence = TRADING_CONFIG.get("min_confidence", 0.55)
        if signal.confidence < min_confidence:
            self.logger.debug(
                "Confidence %.2f below global min %.2f — skipping",
                signal.confidence, min_confidence,
            )
            return None

        market_odds = market["current_odds"].get(signal.outcome, 0.5)
        bet_size = _kelly_bet(self.bankroll, signal.confidence, market_odds)

        if bet_size <= 0:
            self.logger.debug("Kelly returned zero bet — skipping")
            return None

        # Deduct bet from estimated bankroll immediately (worst-case reserve)
        with self._lock:
            self.bankroll = round(self.bankroll - bet_size, 2)
            self.trade_count += 1

        trade = {
            "timestamp":       datetime.now(timezone.utc).isoformat(),
            "market_id":       market["id"],
            "market_question": market["question"],
            "market_url":      market.get("url", ""),
            "strategy":        self.strategy_name,
            "outcome_bet":     signal.outcome,
            "confidence":      round(signal.confidence, 4),
            "bet_size":        bet_size,
            "market_odds":     market_odds,
            "simulated_result": "pending",
            "pnl":             0.0,
            "bankroll_after":  self.bankroll,
            "rationale":       signal.rationale,
            "raw_sentiment":   round(signal.raw_sentiment, 4),
            "status":          "pending",
            "resolved_outcome": None,
            "resolution_time":  None,
        }

        self._log_trade(trade)

        self.logger.info(
            "[%s] PENDING | %s | bet=$%.2f conf=%.2f odds=%.2f | waiting for resolution",
            self.strategy_name, market["id"], bet_size, signal.confidence, market_odds,
        )

        return trade

    def _log_trade(self, trade: Dict) -> None:
        """Insert a pending trade record into SQLite."""
        conn = _get_conn(self.db_path)
        conn.execute("""
            INSERT INTO trades (
                timestamp, market_id, market_question, market_url, strategy,
                outcome_bet, confidence, bet_size, market_odds,
                simulated_result, pnl, bankroll_after, rationale,
                raw_sentiment, status, resolved_outcome, resolution_time
            ) VALUES (
                :timestamp, :market_id, :market_question, :market_url, :strategy,
                :outcome_bet, :confidence, :bet_size, :market_odds,
                :simulated_result, :pnl, :bankroll_after, :rationale,
                :raw_sentiment, :status, :resolved_outcome, :resolution_time
            )
        """, trade)
        conn.commit()
