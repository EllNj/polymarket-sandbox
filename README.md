# PolyMarket Trading Bot Sandbox

Paper-trading framework with 3 concurrent strategy bots, real sentiment analysis,
SQLite trade logging, automatic feedback tuning, and a Matplotlib analytics dashboard.
**No paid APIs required to start.**

---

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run all bots (sandbox mode — no real money, no real API keys needed)
python main.py

# 3. View analytics dashboard anytime
python -m modules.analytics

# Save chart to PNG instead of opening a window
python -m modules.analytics --save dashboard.png
```

---

## Project Structure

```
polymarket_sandbox/
├── main.py                        # Entry point — runs all bots concurrently
├── config.py                      # All API keys, trading params, market definitions
├── requirements.txt
│
├── modules/
│   ├── data_fetcher.py            # RSS (free), NewsAPI, Reddit
│   ├── sentiment.py               # VADER sentiment → confidence score
│   ├── paper_trader.py            # Kelly sizing, SQLite logging, outcome simulation
│   ├── feedback.py                # Auto-tunes strategy params after N trades
│   ├── analytics.py               # Matplotlib dashboard + text summary
│   │
│   └── strategies/
│       ├── base.py                # Abstract BaseStrategy — read this before adding one
│       ├── sentiment_weighted.py  # Strategy 1: weighted multi-source sentiment
│       ├── trend_following.py     # Strategy 2: momentum in rolling sentiment window
│       └── contrarian.py         # Strategy 3: bet against extreme sentiment
│
├── data/
│   └── trades.db                  # Auto-created SQLite database
└── logs/
    └── sandbox.log                # Persistent run log
```

---

## Configuring API Keys (all optional)

Edit `config.py`. The bot runs without any keys (RSS feeds always work).

| Source | Key name | Where to get it | Free tier |
|--------|----------|-----------------|-----------|
| NewsAPI | `newsapi` | https://newsapi.org/register | 100 req/day |
| Reddit | `reddit_client_id` + `reddit_client_secret` | https://www.reddit.com/prefs/apps | Generous |

---

## Adding a New Strategy

1. Create `modules/strategies/my_strategy.py`:

```python
from modules.strategies.base import BaseStrategy, TradeSignal
from modules.sentiment import analyze

class MyStrategy(BaseStrategy):
    name = "my_strategy"

    def evaluate(self, market, articles):
        result = analyze(articles)
        # ... your logic ...
        return TradeSignal(
            should_trade=True,
            outcome="Yes",
            confidence=0.68,
            rationale="My reason",
            raw_sentiment=result.score,
        )
```

2. Register it in `modules/strategies/__init__.py`:

```python
from .my_strategy import MyStrategy

ALL_STRATEGIES = [
    SentimentWeightedStrategy,
    TrendFollowingStrategy,
    ContrarianStrategy,
    MyStrategy,   # ← add here
]
```

3. Add default params to `config.py` under `STRATEGY_PARAMS["my_strategy"]`.

That's all — the main loop and feedback system pick it up automatically.

---

## Adding New Markets

Add entries to `DUMMY_MARKETS` in `config.py`:

```python
{
    "id": "m009",
    "question": "Will X happen by date Y?",
    "outcomes": ["Yes", "No"],
    "current_odds": {"Yes": 0.40, "No": 0.60},
    "keywords": ["keyword1", "keyword2", "keyword3"],
}
```

---

## Enabling DistilBERT (optional, more accurate)

```bash
pip install transformers torch
```

Then uncomment the DistilBERT block at the bottom of `modules/sentiment.py`
and swap `analyze()` calls to `analyze_distilbert()`.

---

## Trade Log Schema

Every trade is stored in `data/trades.db` (SQLite), `trades` table:

| Column | Description |
|--------|-------------|
| `market_question` | Full text of the market |
| `strategy` | Which strategy placed the trade |
| `confidence` | Model confidence at time of trade |
| `bet_size` | Simulated USD bet (Kelly-sized) |
| `simulated_result` | `win` or `loss` |
| `pnl` | Profit or loss in USD |
| `bankroll_after` | Running bankroll after this trade |
| `rationale` | Human-readable explanation from the strategy |

Query it directly:
```bash
sqlite3 data/trades.db "SELECT strategy, COUNT(*), AVG(confidence), SUM(pnl) FROM trades GROUP BY strategy;"
```

---

## Feedback Loop

Every `trades_before_feedback` trades (default: 10), the feedback module:
- Computes win rate, average P&L, and confidence-accuracy correlation
- If win rate < 45%: raises `confidence_threshold` (more selective)
- If win rate > 65% with positive P&L: lowers threshold (more permissive)

Tune the aggressiveness in `modules/feedback.py → suggest_param_updates()`.

---

## Moving to Real Trades

When you're ready to trade real money:
1. Integrate with PolyMarket's CLOB API (https://docs.polymarket.com)
2. Replace `_simulate_outcome()` in `paper_trader.py` with real order placement
3. Set `sandbox_mode = False` in `config.py`
4. Keep the feedback loop and analytics — they work identically on real data
