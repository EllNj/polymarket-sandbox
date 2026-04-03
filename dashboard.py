"""
dashboard.py — Live Streamlit dashboard for the PolyMarket sandbox.

Run alongside main.py in a second terminal:
    streamlit run dashboard.py

The dashboard reads from the SQLite database in real time —
no need to stop the bot. Auto-refreshes every 5 seconds.
"""

import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

from config import TRADING_CONFIG

DB_PATH = TRADING_CONFIG["db_path"]
REFRESH_INTERVAL = 5  # seconds

# ---------------------------------------------------------------------------
# PAGE CONFIG
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="PolyMarket Sandbox",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Password protection
_password = os.getenv("DASHBOARD_PASSWORD", "")
if _password:
    pwd = st.text_input("Password", type="password")
    if pwd != _password:
        st.stop()

# ---------------------------------------------------------------------------
# DATA LOADING
# ---------------------------------------------------------------------------

@st.cache_data(ttl=REFRESH_INTERVAL)
def load_trades() -> pd.DataFrame:
    """Load all trades from SQLite. Cached for REFRESH_INTERVAL seconds."""
    if not Path(DB_PATH).exists():
        return pd.DataFrame()
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("SELECT * FROM trades ORDER BY timestamp ASC", conn)
    conn.close()
    if df.empty:
        return df
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df["win"] = (df["simulated_result"] == "win").astype(int)
    df["cumulative_pnl"] = df.groupby("strategy")["pnl"].cumsum()
    return df


def load_log_tail(n: int = 40) -> list[str]:
    """Read the last N lines from the log file."""
    log_file = Path(TRADING_CONFIG["log_dir"]) / "sandbox.log"
    if not log_file.exists():
        return ["Log file not found — is main.py running?"]
    with open(log_file, "r", encoding="utf-8") as f:
        lines = f.readlines()
    return [l.rstrip() for l in lines[-n:]]


# ---------------------------------------------------------------------------
# SIDEBAR
# ---------------------------------------------------------------------------
with st.sidebar:
    st.title("PolyMarket Sandbox")
    st.caption("Paper trading · Live sentiment · Free APIs")
    st.divider()

    df_all = load_trades()
    if not df_all.empty:
        strategies = sorted(df_all["strategy"].unique().tolist())
        selected = st.multiselect(
            "Filter strategies",
            options=strategies,
            default=strategies,
        )
        df_all = df_all[df_all["strategy"].isin(selected)]
    else:
        st.info("No trades yet — run `python main.py` to start.")
        selected = []

    st.divider()
    st.caption(f"Auto-refreshing every {REFRESH_INTERVAL}s")
    st.caption(f"DB: `{DB_PATH}`")

# ---------------------------------------------------------------------------
# HEADER METRICS
# ---------------------------------------------------------------------------
st.title("📈 PolyMarket Sandbox Dashboard")

if df_all.empty:
    st.warning("No trade data found. Start `main.py` in another terminal to begin.")
    st.stop()

col1, col2, col3, col4, col5 = st.columns(5)

resolved = df_all[df_all["status"] == "resolved"] if "status" in df_all.columns else df_all
pending_count = len(df_all) - len(resolved)

total_trades = len(df_all)
total_pnl = resolved["pnl"].sum() if not resolved.empty else 0.0
overall_win_rate = resolved["win"].mean() * 100 if not resolved.empty else 0.0
best_strategy = resolved.groupby("strategy")["pnl"].sum().idxmax() if not resolved.empty else "—"
active_strategies = df_all["strategy"].nunique()

col1.metric("Total Trades", total_trades, delta=f"{pending_count} pending")
col2.metric("Realised P&L", f"${total_pnl:+.2f}", help="Resolved trades only")
col3.metric("Win Rate", f"{overall_win_rate:.1f}%", help="Resolved trades only")
col4.metric("Best Strategy", best_strategy)
col5.metric("Pending", pending_count, help="Awaiting real market resolution")

st.divider()

# ---------------------------------------------------------------------------
# ROW 1: Cumulative P&L + Win Rate per Strategy
# ---------------------------------------------------------------------------
row1_left, row1_right = st.columns([2, 1])

with row1_left:
    st.subheader("Cumulative P&L")
    pnl_pivot = df_all.pivot_table(
        index="timestamp", columns="strategy", values="cumulative_pnl", aggfunc="last"
    ).ffill()
    st.line_chart(pnl_pivot, height=280)

with row1_right:
    st.subheader("Win Rate per Strategy")
    win_rates = (
        df_all.groupby("strategy")["win"]
        .mean()
        .mul(100)
        .round(1)
        .reset_index()
        .rename(columns={"win": "Win %"})
    )
    # Colour-code: green if >50%, red if <50%
    for _, row in win_rates.iterrows():
        delta_color = "normal" if row["Win %"] >= 50 else "inverse"
        st.metric(
            label=row["strategy"],
            value=f"{row['Win %']:.1f}%",
            delta=f"{row['Win %'] - 50:.1f}% vs 50% baseline",
            delta_color=delta_color,
        )

st.divider()

# ---------------------------------------------------------------------------
# ROW 2: Per-Strategy Bankroll + Confidence Distribution
# ---------------------------------------------------------------------------
row2_left, row2_right = st.columns(2)

with row2_left:
    st.subheader("Bankroll per Strategy")
    bankroll = df_all.sort_values("timestamp").pivot_table(
        index="timestamp", columns="strategy", values="bankroll_after", aggfunc="last"
    ).ffill()
    st.line_chart(bankroll, height=240)

with row2_right:
    st.subheader("Avg Confidence — Wins vs Losses")
    conf_compare = (
        df_all.groupby(["strategy", "simulated_result"])["confidence"]
        .mean()
        .mul(100)
        .round(1)
        .unstack(fill_value=0)
        .reset_index()
    )
    st.dataframe(
        conf_compare,
        use_container_width=True,
        hide_index=True,
        column_config={
            "strategy": "Strategy",
            "win": st.column_config.NumberColumn("Avg Conf (Win)", format="%.1f%%"),
            "loss": st.column_config.NumberColumn("Avg Conf (Loss)", format="%.1f%%"),
        },
    )

    st.divider()
    st.subheader("P&L per Market")
    market_pnl = (
        df_all.groupby("market_id")["pnl"]
        .sum()
        .round(2)
        .reset_index()
        .sort_values("pnl", ascending=False)
    )
    st.dataframe(market_pnl, use_container_width=True, hide_index=True)

st.divider()

# ---------------------------------------------------------------------------
# ROW 3: Recent Trades Table
# ---------------------------------------------------------------------------
st.subheader("Recent Trades")
recent = df_all.sort_values("timestamp", ascending=False).head(50).copy()
recent["timestamp"] = recent["timestamp"].dt.strftime("%H:%M:%S")
recent["result"] = recent["simulated_result"].map({"win": "✅ Win", "loss": "❌ Loss", "pending": "⏳ Pending"})
if "status" in recent.columns:
    recent.loc[recent["status"] == "closed_early", "result"] = recent.loc[recent["status"] == "closed_early", "result"] + " 🚪"

# Build clickable market link
if "market_url" in recent.columns and "market_question" in recent.columns:
    recent["Market"] = recent.apply(
        lambda r: f"[{r['market_question'][:55]}...]({r['market_url']})" if r.get("market_url") else r["market_question"][:55],
        axis=1,
    )
else:
    recent["Market"] = recent.get("market_question", recent["market_id"]).str[:55]

# Live price column — entry odds + link to check current
recent["Entry Odds"] = recent["market_odds"].apply(lambda x: f"{x:.0%}")
recent["Live Price"] = recent.apply(
    lambda r: f"[Check ↗]({r.get('market_url', '')})" if r.get("market_url") else "—",
    axis=1,
)

st.dataframe(
    recent[[
        "timestamp", "strategy", "Market", "outcome_bet",
        "confidence", "bet_size", "Entry Odds", "Live Price",
        "pnl", "result", "bankroll_after"
    ]].rename(columns={
        "timestamp": "Time",
        "strategy": "Strategy",
        "outcome_bet": "Bet",
        "confidence": "Confidence",
        "bet_size": "Bet ($)",
        "pnl": "P&L ($)",
        "result": "Result",
        "bankroll_after": "Bankroll ($)",
    }),
    use_container_width=True,
    hide_index=True,
    column_config={
        "Market": st.column_config.LinkColumn("Market", display_text="(.+)"),
        "Live Price": st.column_config.LinkColumn("Live Price"),
        "Confidence": st.column_config.ProgressColumn(
            "Confidence", min_value=0, max_value=1, format="%.2f"
        ),
        "P&L ($)": st.column_config.NumberColumn("P&L ($)", format="$%.2f"),
        "Bet ($)": st.column_config.NumberColumn("Bet ($)", format="$%.2f"),
        "Bankroll ($)": st.column_config.NumberColumn("Bankroll ($)", format="$%.2f"),
    },
)

st.divider()

# ---------------------------------------------------------------------------
# ROW 4: Live Log Feed
# ---------------------------------------------------------------------------
st.subheader("Live Log")
log_lines = load_log_tail(40)
log_text = "\n".join(log_lines)
st.code(log_text, language=None)

# ---------------------------------------------------------------------------
# AUTO-REFRESH
# ---------------------------------------------------------------------------
time.sleep(REFRESH_INTERVAL)
st.rerun()
