"""
main.py — Entry point for the PolyMarket sandbox.

How it works with live markets:
  - On startup: fetches live markets from PolyMarket, evaluates each once
  - Then waits POLL_INTERVAL seconds and fetches fresh markets again
  - Runs indefinitely until you Ctrl+C
  - Resolver checks for real outcomes every RESOLVER_INTERVAL seconds
  - Feedback loop runs after every N trades per strategy

Usage:
    python main.py
"""

import logging
import os
import threading
import time
from datetime import datetime, timezone, timedelta

from config import DUMMY_MARKETS, STRATEGY_PARAMS, TRADING_CONFIG
from modules.data_fetcher import fetch_all
from modules.feedback import run_feedback_loop
from modules.polymarket_client import fetch_live_markets
from modules.resolver import run_resolver
from modules.position_manager import check_early_exits
from modules.reporter import send_daily_report
from modules.paper_trader import PaperTrader, init_db
from modules.strategies import ALL_STRATEGIES

# ---------------------------------------------------------------------------
# RUN CONFIGURATION
# ---------------------------------------------------------------------------
POLL_INTERVAL    = 3600   # Fetch fresh markets and re-evaluate every N seconds (1 hour)
SLEEP_SEC        = 3      # Pause between market evaluations (rate-limit friendly)
RESOLVER_INTERVAL = 3600  # Check PolyMarket for real resolutions every N seconds
SHOW_CHART       = False  # Set True to open Matplotlib chart on Ctrl+C (local only)

# ---------------------------------------------------------------------------
# LOGGING SETUP
# ---------------------------------------------------------------------------
os.makedirs(TRADING_CONFIG["log_dir"], exist_ok=True)
os.makedirs(os.path.dirname(TRADING_CONFIG["db_path"]), exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(
            os.path.join(TRADING_CONFIG["log_dir"], "sandbox.log"),
            encoding="utf-8",
        ),
    ],
)
logger = logging.getLogger("main")


# ---------------------------------------------------------------------------
# MARKET EVALUATION — runs once per poll cycle per strategy
# ---------------------------------------------------------------------------

def evaluate_markets(strategy, trader, markets: list) -> None:
    """
    Evaluate every market once: fetch articles, get signal, execute trade.
    Skips markets already traded in this session to avoid duplicate bets.
    """
    feedback_interval = TRADING_CONFIG.get("trades_before_feedback", 10)

    for market in markets:
        logger.info("[%s] Evaluating: %s", strategy.name, market.get("question", market["id"])[:60])

        articles = fetch_all(market["keywords"])

        if not articles:
            logger.warning("[%s] No articles for market %s — skipping", strategy.name, market["id"])
            continue

        signal = strategy.evaluate(market, articles)
        trader.execute(market, signal)

        if trader.trade_count > 0 and trader.trade_count % feedback_interval == 0:
            logger.info("[%s] Running feedback loop at trade #%d", strategy.name, trader.trade_count)
            run_feedback_loop([(strategy, trader)])

        time.sleep(SLEEP_SEC)


# ---------------------------------------------------------------------------
# STRATEGY BOT THREAD — runs indefinitely, polling on schedule
# ---------------------------------------------------------------------------

def run_strategy_bot(strategy_cls, params: dict, all_pairs: list) -> None:
    """
    Runs indefinitely. Each cycle:
      1. Fetch fresh live markets
      2. Evaluate each market once
      3. Sleep until next poll
    """
    strategy = strategy_cls(params)
    trader = PaperTrader(strategy.name)
    all_pairs.append((strategy, trader))

    logger.info("Bot started: %s | bankroll=$%.2f", strategy.name, trader.bankroll)

    cycle = 0
    while True:
        cycle += 1
        logger.info("[%s] === Cycle %d — fetching live markets ===", strategy.name, cycle)

        if TRADING_CONFIG.get("use_live_markets", True):
            markets = fetch_live_markets(
                limit=TRADING_CONFIG.get("live_market_limit", 15),
                min_volume=TRADING_CONFIG.get("live_min_volume", 1000.0),
            )
            if not markets:
                logger.warning("[%s] No live markets returned — falling back to dummy", strategy.name)
                markets = DUMMY_MARKETS
        else:
            markets = DUMMY_MARKETS

        logger.info("[%s] Evaluating %d markets", strategy.name, len(markets))
        evaluate_markets(strategy, trader, markets)

        logger.info("[%s] Cycle %d complete — sleeping %ds until next poll", strategy.name, cycle, POLL_INTERVAL)
        time.sleep(POLL_INTERVAL)


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main() -> None:
    logger.info("PolyMarket Sandbox starting — press Ctrl+C to stop")

    init_db(TRADING_CONFIG["db_path"])

    all_pairs: list = []
    threads = []

    for strategy_cls in ALL_STRATEGIES:
        params = STRATEGY_PARAMS.get(strategy_cls.name, {}).copy()
        t = threading.Thread(
            target=run_strategy_bot,
            args=(strategy_cls, params, all_pairs),
            name=f"bot-{strategy_cls.name}",
            daemon=True,
        )
        threads.append(t)
        t.start()
        time.sleep(0.5)  # Stagger starts slightly

    # Resolver + position manager run hourly in background
    resolver_stop = threading.Event()
    def _resolver_loop():
        while not resolver_stop.is_set():
            run_resolver()
            check_early_exits()
            resolver_stop.wait(RESOLVER_INTERVAL)

    resolver_thread = threading.Thread(target=_resolver_loop, name="resolver", daemon=True)
    resolver_thread.start()
    logger.info("Resolver + position manager running every %ds", RESOLVER_INTERVAL)

    # Daily email report at 8am UTC
    report_stop = threading.Event()
    def _reporter_loop():
        while not report_stop.is_set():
            now = datetime.now(timezone.utc)
            # Calculate seconds until next 8am UTC
            next_8am = now.replace(hour=8, minute=0, second=0, microsecond=0)
            if now >= next_8am:
                next_8am += timedelta(days=1)
            sleep_secs = (next_8am - now).total_seconds()
            logger.info("Next email report in %.0fh at %s UTC", sleep_secs/3600, next_8am.strftime("%H:%M"))
            report_stop.wait(sleep_secs)
            if not report_stop.is_set():
                send_daily_report()

    report_thread = threading.Thread(target=_reporter_loop, name="reporter", daemon=True)
    report_thread.start()

    # Run until Ctrl+C
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("Shutting down…")
        resolver_stop.set()

        if all_pairs:
            run_feedback_loop(all_pairs)

        from modules.analytics import print_summary, show_dashboard  # noqa: PLC0415
        print_summary()

        if SHOW_CHART:
            show_dashboard()


if __name__ == "__main__":
    main()
