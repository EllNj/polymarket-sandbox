"""
reporter.py — Daily email summary of bot performance.

Sends a morning email with:
  - P&L summary per strategy
  - Trades placed in last 24h
  - Early exits taken
  - Pending positions still open
  - Which strategy is winning

Setup:
  1. Use a Gmail account
  2. Enable 2FA at myaccount.google.com
  3. Create an App Password: myaccount.google.com/apppasswords
  4. Add to .env:
       EMAIL_SENDER=yourgmail@gmail.com
       EMAIL_PASSWORD=your16charapppassword
       EMAIL_RECIPIENT=youremail@gmail.com
"""

import logging
import os
import smtplib
import sqlite3
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from config import TRADING_CONFIG

logger = logging.getLogger(__name__)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


def _load_email_config() -> tuple[str, str, str] | None:
    """Load email credentials from environment. Returns None if not configured."""
    sender    = os.getenv("EMAIL_SENDER", "")
    password  = os.getenv("EMAIL_PASSWORD", "")
    recipient = os.getenv("EMAIL_RECIPIENT", "")

    if not all([sender, password, recipient]):
        logger.warning("Email not configured — set EMAIL_SENDER, EMAIL_PASSWORD, EMAIL_RECIPIENT in .env")
        return None

    return sender, password, recipient


def _fetch_report_data(db_path: str) -> dict:
    """Pull all stats needed for the report from SQLite."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    since_24h = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()

    # Overall stats per strategy
    strategies = conn.execute("""
        SELECT
            strategy,
            COUNT(*) as total_trades,
            SUM(CASE WHEN status != 'pending' THEN 1 ELSE 0 END) as resolved,
            SUM(CASE WHEN simulated_result = 'win' THEN 1 ELSE 0 END) as wins,
            SUM(CASE WHEN status != 'pending' THEN pnl ELSE 0 END) as total_pnl,
            MAX(bankroll_after) as bankroll,
            SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) as pending
        FROM trades
        GROUP BY strategy
    """).fetchall()

    # Last 24h trades
    recent = conn.execute("""
        SELECT strategy, market_question, outcome_bet, confidence,
               bet_size, pnl, simulated_result, status
        FROM trades
        WHERE timestamp > ?
        ORDER BY timestamp DESC
    """, (since_24h,)).fetchall()

    # Early exits in last 24h
    early_exits = conn.execute("""
        SELECT strategy, market_id, pnl
        FROM trades
        WHERE status = 'closed_early' AND timestamp > ?
    """, (since_24h,)).fetchall()

    # Best and worst resolved trades ever
    best = conn.execute("""
        SELECT strategy, market_question, pnl
        FROM trades WHERE status != 'pending'
        ORDER BY pnl DESC LIMIT 1
    """).fetchone()

    worst = conn.execute("""
        SELECT strategy, market_question, pnl
        FROM trades WHERE status != 'pending'
        ORDER BY pnl ASC LIMIT 1
    """).fetchone()

    conn.close()
    return {
        "strategies": [dict(r) for r in strategies],
        "recent": [dict(r) for r in recent],
        "early_exits": [dict(r) for r in early_exits],
        "best": dict(best) if best else None,
        "worst": dict(worst) if worst else None,
    }


def _build_email_body(data: dict) -> str:
    """Format the report data into a readable email."""
    now = datetime.now(timezone.utc).strftime("%A %d %B %Y")
    lines = [
        f"PolyMarket Sandbox — Daily Report",
        f"{now}",
        "=" * 50,
        "",
    ]

    # Strategy summary table
    lines.append("STRATEGY PERFORMANCE")
    lines.append("-" * 50)
    for s in data["strategies"]:
        resolved = s["resolved"] or 0
        wins = s["wins"] or 0
        win_rate = (wins / resolved * 100) if resolved > 0 else 0
        lines.append(
            f"{s['strategy']:<25} "
            f"Trades: {s['total_trades']:>3}  "
            f"Win: {win_rate:>5.1f}%  "
            f"P&L: ${s['total_pnl'] or 0:>+8.2f}  "
            f"Bankroll: ${s['bankroll'] or 0:>8.2f}  "
            f"Pending: {s['pending']:>2}"
        )
    lines.append("")

    # Last 24h activity
    recent = data["recent"]
    lines.append(f"LAST 24 HOURS ({len(recent)} trades)")
    lines.append("-" * 50)
    if recent:
        for t in recent[:10]:  # cap at 10
            status = t["simulated_result"].upper() if t["simulated_result"] != "pending" else "PENDING"
            lines.append(
                f"  [{t['strategy'][:12]:<12}] {t['market_question'][:45]:<45}  "
                f"Bet:{t['outcome_bet']}  Conf:{t['confidence']:.2f}  "
                f"${t['bet_size']:.0f}  {status}  P&L:${t['pnl']:+.2f}"
            )
        if len(recent) > 10:
            lines.append(f"  ... and {len(recent) - 10} more")
    else:
        lines.append("  No trades in last 24 hours")
    lines.append("")

    # Early exits
    exits = data["early_exits"]
    if exits:
        lines.append(f"EARLY EXITS ({len(exits)} positions closed)")
        lines.append("-" * 50)
        for e in exits:
            lines.append(f"  [{e['strategy']}] market {e['market_id']}  P&L: ${e['pnl']:+.2f}")
        lines.append("")

    # Best / worst
    lines.append("ALL-TIME HIGHLIGHTS")
    lines.append("-" * 50)
    if data["best"]:
        b = data["best"]
        lines.append(f"  Best trade:  ${b['pnl']:+.2f}  [{b['strategy']}]  {b['market_question'][:50]}")
    if data["worst"]:
        w = data["worst"]
        lines.append(f"  Worst trade: ${w['pnl']:+.2f}  [{w['strategy']}]  {w['market_question'][:50]}")

    lines.append("")
    lines.append("Bot is running. Next report tomorrow morning.")
    return "\n".join(lines)


def send_daily_report(db_path: Optional[str] = None) -> bool:
    """
    Fetch stats from the database and send the daily email report.
    Returns True if sent successfully, False otherwise.
    """
    config = _load_email_config()
    if not config:
        return False

    sender, password, recipient = config
    db = db_path or TRADING_CONFIG["db_path"]

    try:
        data = _fetch_report_data(db)
    except Exception as exc:
        logger.error("Failed to fetch report data: %s", exc)
        return False

    body = _build_email_body(data)
    date_str = datetime.now(timezone.utc).strftime("%d %b %Y")

    msg = MIMEMultipart()
    msg["From"]    = sender
    msg["To"]      = recipient
    msg["Subject"] = f"PolyMarket Bot — Daily Report {date_str}"
    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(sender, password)
            server.sendmail(sender, recipient, msg.as_string())
        logger.info("Daily report sent to %s", recipient)
        return True
    except Exception as exc:
        logger.error("Failed to send email: %s", exc)
        return False
