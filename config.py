"""
config.py — Central configuration for all API keys, trading parameters,
and strategy settings.

API keys are loaded from a .env file (never committed to git).
Copy .env.example to .env and fill in your keys.
"""

import os
from dotenv import load_dotenv
load_dotenv()

# ---------------------------------------------------------------------------
# API KEYS — replace placeholder strings with your actual keys
# ---------------------------------------------------------------------------
API_KEYS = {
    "guardian": os.getenv("GUARDIAN_API_KEY", ""),
    "nyt":      os.getenv("NYT_API_KEY", ""),
    "reddit_client_id":     os.getenv("REDDIT_CLIENT_ID", ""),
    "reddit_client_secret": os.getenv("REDDIT_CLIENT_SECRET", ""),
    "reddit_user_agent":    "polymarket_sandbox_bot/1.0",
}

# ---------------------------------------------------------------------------
# RSS FEEDS — no API key required; add or remove freely
# ---------------------------------------------------------------------------
RSS_FEEDS = [
    "https://feeds.reuters.com/reuters/topNews",
    "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
    "https://www.theguardian.com/world/rss",
    "https://feeds.bbci.co.uk/news/rss.xml",
    "https://feeds.a.dj.com/rss/RSSWorldNews.xml",   # Wall Street Journal
]

# ---------------------------------------------------------------------------
# TRADING ENGINE CONFIG
# ---------------------------------------------------------------------------
TRADING_CONFIG = {
    "initial_bankroll": 1000.0,   # Starting paper money in USD
    "max_bet_fraction": 0.05,     # Never risk more than 5% per trade
    "kelly_fraction": 0.25,       # Fractional Kelly (0.25 = quarter-Kelly, safer)
    "min_confidence": 0.55,       # Skip trade if confidence below this
    "trades_before_feedback": 10, # Run feedback loop every N completed trades
    "sandbox_mode": True,         # True = simulate outcomes; False = use real data
    # On Railway, set RAILWAY_VOLUME_MOUNT_PATH env var to /data
    # Locally it falls back to the data/ folder
    "db_path": os.path.join(os.getenv("RAILWAY_VOLUME_MOUNT_PATH", "data"), "trades.db"),
    "log_dir": os.getenv("LOG_DIR", "logs"),

    # --- Data source toggles ---
    "reddit_enabled": False,   # Set True if Reddit works for you; False to skip entirely

    # --- Early exit thresholds ---
    "take_profit_threshold": 0.25,  # Close position if odds move 25% in your favour
    "stop_loss_threshold":   0.40,  # Cut losses if odds move 40% against you

    # --- Live PolyMarket data (free, no API key needed) ---
    "use_live_markets": True,     # True = pull real markets from PolyMarket API
                                   # False = use DUMMY_MARKETS from this file
    "live_market_limit": 15,      # How many live markets to fetch per run
    "live_min_volume": 1000.0,    # Skip markets with less than this USD volume
}

# ---------------------------------------------------------------------------
# STRATEGY PARAMETERS — feedback loop will tune these automatically
# ---------------------------------------------------------------------------
STRATEGY_PARAMS = {
    "sentiment_weighted": {
        # Weight given to each data source when combining sentiment scores
        "source_weights": {"rss": 0.30, "guardian": 0.25, "nyt": 0.20, "stocktwits": 0.15, "reddit": 0.10},
        "confidence_threshold": 0.60,
        "lookback_articles": 10,
    },
    "trend_following": {
        "momentum_threshold": 0.08,    # Minimum sentiment shift to confirm trend
        "confidence_threshold": 0.58,
        "trend_window": 5,             # Number of sentiment samples to track trend
    },
    "contrarian": {
        # Only bet against sentiment when it exceeds this extreme level
        "reversal_threshold": 0.55,    # Lowered from 0.75 — was too rarely triggered
        "contrarian_strength": 0.85,
        "confidence_threshold": 0.57,
    },
}

# ---------------------------------------------------------------------------
# DUMMY MARKET QUESTIONS — used when sandbox_mode=True
# Replace or extend with your own hypothetical markets.
# current_odds: implied probability for each outcome (must sum to ~1.0)
# ---------------------------------------------------------------------------
DUMMY_MARKETS = [
    {
        "id": "m001",
        "question": "Will the US Federal Reserve cut interest rates in Q2 2026?",
        "outcomes": ["Yes", "No"],
        "current_odds": {"Yes": 0.42, "No": 0.58},
        "keywords": ["federal reserve", "interest rates", "fed", "rate cut", "fomc"],
    },
    {
        "id": "m002",
        "question": "Will the S&P 500 close above 5,500 by end of April 2026?",
        "outcomes": ["Yes", "No"],
        "current_odds": {"Yes": 0.51, "No": 0.49},
        "keywords": ["S&P 500", "stock market", "equities", "nasdaq", "dow jones"],
    },
    {
        "id": "m003",
        "question": "Will Bitcoin exceed $100,000 before June 2026?",
        "outcomes": ["Yes", "No"],
        "current_odds": {"Yes": 0.38, "No": 0.62},
        "keywords": ["bitcoin", "BTC", "crypto", "cryptocurrency"],
    },
    {
        "id": "m004",
        "question": "Will the EU reach a new trade deal with the US in 2026?",
        "outcomes": ["Yes", "No"],
        "current_odds": {"Yes": 0.30, "No": 0.70},
        "keywords": ["EU", "trade deal", "tariffs", "european union", "trade war"],
    },
    {
        "id": "m005",
        "question": "Will OpenAI release GPT-5 before September 2026?",
        "outcomes": ["Yes", "No"],
        "current_odds": {"Yes": 0.65, "No": 0.35},
        "keywords": ["openai", "gpt-5", "artificial intelligence", "ai model", "llm"],
    },
    {
        "id": "m006",
        "question": "Will US unemployment rise above 4.5% in 2026?",
        "outcomes": ["Yes", "No"],
        "current_odds": {"Yes": 0.25, "No": 0.75},
        "keywords": ["unemployment", "jobs report", "labor market", "layoffs", "recession"],
    },
    {
        "id": "m007",
        "question": "Will there be a US government shutdown before July 2026?",
        "outcomes": ["Yes", "No"],
        "current_odds": {"Yes": 0.20, "No": 0.80},
        "keywords": ["government shutdown", "congress", "budget", "continuing resolution"],
    },
    {
        "id": "m008",
        "question": "Will Elon Musk remain CEO of Tesla through end of 2026?",
        "outcomes": ["Yes", "No"],
        "current_odds": {"Yes": 0.78, "No": 0.22},
        "keywords": ["elon musk", "tesla", "ceo", "board"],
    },
]
