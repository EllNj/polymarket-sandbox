"""
analytics.py — Visualise performance across all strategies using Matplotlib.

Run directly:
    python -m modules.analytics

Or call show_dashboard() from your own script.

Charts produced:
  1. Accuracy (win rate) per strategy — bar chart
  2. Cumulative P&L over time per strategy — line chart
  3. Confidence score vs outcome scatter — one subplot per strategy
"""

import logging
import sqlite3
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import pandas as pd

from config import TRADING_CONFIG

logger = logging.getLogger(__name__)


def load_all_trades(db_path: str) -> pd.DataFrame:
    """Load every trade from the database into a DataFrame."""
    try:
        conn = sqlite3.connect(db_path)
        df = pd.read_sql_query(
            "SELECT * FROM trades ORDER BY timestamp ASC", conn
        )
        conn.close()
        df["win"] = (df["simulated_result"] == "win").astype(int)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        return df
    except Exception as exc:
        logger.error("Failed to load trades: %s", exc)
        return pd.DataFrame()


def show_dashboard(db_path: str | None = None, save_path: str | None = None) -> None:
    """
    Load all trades and render the analytics dashboard.
    Pass save_path to write a PNG instead of opening a window.
    """
    db = db_path or TRADING_CONFIG["db_path"]
    df = load_all_trades(db)

    if df.empty:
        print("No trades found in the database. Run main.py first to generate trades.")
        return

    strategies = df["strategy"].unique().tolist()
    n_strats = len(strategies)

    # Colour palette
    colours = plt.cm.tab10.colors

    fig = plt.figure(figsize=(16, 12))
    fig.suptitle("PolyMarket Sandbox — Strategy Analytics", fontsize=16, fontweight="bold")
    gs = gridspec.GridSpec(3, max(n_strats, 1), figure=fig, hspace=0.45, wspace=0.35)

    # -----------------------------------------------------------------------
    # Chart 1: Win Rate (accuracy) per strategy — top row, full width
    # -----------------------------------------------------------------------
    ax_acc = fig.add_subplot(gs[0, :])
    win_rates = {
        s: df[df["strategy"] == s]["win"].mean() * 100 for s in strategies
    }
    bars = ax_acc.bar(
        list(win_rates.keys()),
        list(win_rates.values()),
        color=[colours[i % 10] for i in range(n_strats)],
        edgecolor="black",
        linewidth=0.8,
    )
    ax_acc.axhline(50, color="red", linestyle="--", linewidth=1, label="50% baseline")
    ax_acc.set_title("Win Rate (Accuracy) per Strategy")
    ax_acc.set_ylabel("Win Rate (%)")
    ax_acc.set_ylim(0, 100)
    ax_acc.legend(fontsize=9)
    for bar, val in zip(bars, win_rates.values()):
        ax_acc.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + 1,
            f"{val:.1f}%",
            ha="center", va="bottom", fontsize=10, fontweight="bold",
        )

    # -----------------------------------------------------------------------
    # Chart 2: Cumulative P&L over time — middle row, full width
    # -----------------------------------------------------------------------
    ax_pnl = fig.add_subplot(gs[1, :])
    for i, strat in enumerate(strategies):
        sub = df[df["strategy"] == strat].sort_values("timestamp")
        cumulative = sub["pnl"].cumsum()
        ax_pnl.plot(
            sub["timestamp"],
            cumulative,
            label=strat,
            color=colours[i % 10],
            linewidth=2,
            marker="o",
            markersize=3,
        )
    ax_pnl.axhline(0, color="black", linestyle="-", linewidth=0.8)
    ax_pnl.set_title("Cumulative P&L Over Time per Strategy")
    ax_pnl.set_ylabel("Cumulative P&L ($)")
    ax_pnl.set_xlabel("Time")
    ax_pnl.legend(fontsize=9)
    ax_pnl.tick_params(axis="x", rotation=30)

    # -----------------------------------------------------------------------
    # Chart 3: Confidence vs Outcome — bottom row, one subplot per strategy
    # -----------------------------------------------------------------------
    for i, strat in enumerate(strategies):
        ax_conf = fig.add_subplot(gs[2, i])
        sub = df[df["strategy"] == strat]
        wins = sub[sub["simulated_result"] == "win"]["confidence"]
        losses = sub[sub["simulated_result"] == "loss"]["confidence"]

        ax_conf.hist(wins, bins=10, alpha=0.6, color="green", label="Win", density=True)
        ax_conf.hist(losses, bins=10, alpha=0.6, color="red", label="Loss", density=True)
        ax_conf.set_title(f"{strat}\nConf. Distribution", fontsize=9)
        ax_conf.set_xlabel("Confidence", fontsize=8)
        ax_conf.set_ylabel("Density", fontsize=8)
        ax_conf.legend(fontsize=7)

        # Correlation annotation
        corr = sub[["confidence", "win"]].corr().iloc[0, 1]
        ax_conf.text(
            0.05, 0.92, f"corr={corr:.2f}",
            transform=ax_conf.transAxes, fontsize=8,
            verticalalignment="top",
            bbox=dict(boxstyle="round", facecolor="wheat", alpha=0.5),
        )

    if save_path:
        fig.savefig(save_path, dpi=150, bbox_inches="tight")
        print(f"Dashboard saved to {save_path}")
    else:
        plt.show()


def print_summary(db_path: str | None = None) -> None:
    """Print a text-based summary table to stdout."""
    db = db_path or TRADING_CONFIG["db_path"]
    df = load_all_trades(db)

    if df.empty:
        print("No trades in database.")
        return

    print("\n" + "=" * 70)
    print("PERFORMANCE SUMMARY")
    print("=" * 70)
    print(f"{'Strategy':<25} {'Trades':>7} {'Win%':>7} {'Tot P&L':>10} {'Avg Bet':>9}")
    print("-" * 70)

    for strat in sorted(df["strategy"].unique()):
        sub = df[df["strategy"] == strat]
        trades = len(sub)
        win_pct = sub["win"].mean() * 100
        total_pnl = sub["pnl"].sum()
        avg_bet = sub["bet_size"].mean()
        print(f"{strat:<25} {trades:>7} {win_pct:>6.1f}% {total_pnl:>+10.2f} {avg_bet:>9.2f}")

    print("=" * 70)


if __name__ == "__main__":
    # Usage: python -m modules.analytics [--save dashboard.png]
    save = None
    if "--save" in sys.argv:
        idx = sys.argv.index("--save")
        save = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else "dashboard.png"

    print_summary()
    show_dashboard(save_path=save)
