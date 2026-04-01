"""
polymarket_client.py — Fetch live market data from PolyMarket's public Gamma API.

No API key or account required. Read-only. Free.
Docs: https://docs.polymarket.com/#gamma-markets-api

Public endpoint: https://gamma-api.polymarket.com/markets
Returns real active markets with live odds/prices.

Usage:
    from modules.polymarket_client import fetch_live_markets
    markets = fetch_live_markets(limit=20)
    # Returns list of dicts in the same format as DUMMY_MARKETS in config.py
"""

import logging
import re
from typing import List, Dict

import requests

logger = logging.getLogger(__name__)

GAMMA_API = "https://gamma-api.polymarket.com"


def fetch_live_markets(
    limit: int = 20,
    min_volume: float = 1000.0,   # Skip tiny/illiquid markets
    binary_only: bool = True,      # Only Yes/No markets (easier to model)
) -> List[Dict]:
    """
    Pull active markets from PolyMarket's Gamma API and convert them
    into the same dict format used by DUMMY_MARKETS in config.py.

    Args:
        limit:       Max number of markets to return
        min_volume:  Skip markets with total volume below this (filters noise)
        binary_only: If True, only return Yes/No binary markets

    Returns list of dicts with keys:
        id, question, outcomes, current_odds, keywords
    """
    try:
        resp = requests.get(
            f"{GAMMA_API}/markets",
            params={
                "closed":      "false",
                "active":      "true",
                "archived":    "false",
                "order":       "volume",       # Sort by trading volume — gets real active markets
                "ascending":   "false",        # Highest volume first
                "limit":       min(limit * 3, 100),
            },
            timeout=15,
            headers={"User-Agent": "polymarket-sandbox-bot/1.0"},
        )
        resp.raise_for_status()
        raw_markets = resp.json()
    except requests.RequestException as exc:
        logger.error("Failed to fetch PolyMarket markets: %s", exc)
        return []

    markets = []
    for m in raw_markets:
        try:
            market = _parse_market(m, binary_only, min_volume)
            if market:
                markets.append(market)
                if len(markets) >= limit:
                    break
        except Exception as exc:
            logger.debug("Skipping market (parse error): %s", exc)
            continue

    logger.info("Fetched %d live markets from PolyMarket", len(markets))
    return markets


def _parse_market(raw: Dict, binary_only: bool, min_volume: float) -> Dict | None:
    """Convert a raw Gamma API market dict into our internal format."""
    question = raw.get("question", "").strip()
    if not question:
        return None

    # Skip already resolved or closed markets
    if raw.get("resolved") or raw.get("closed") or raw.get("archived"):
        return None

    # Filter out low-volume markets
    volume = float(raw.get("volume", 0) or 0)
    if volume < min_volume:
        return None

    # Skip markets with no future end date
    end_date = raw.get("endDate") or raw.get("endDateIso") or ""
    if end_date:
        from datetime import datetime, timezone  # noqa: PLC0415
        try:
            end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
            if end_dt < datetime.now(timezone.utc):
                return None  # Already expired
        except ValueError:
            pass

    # Parse outcomes and prices
    outcomes_raw = raw.get("outcomes", "")
    prices_raw = raw.get("outcomePrices", "")

    # Gamma API returns these as JSON strings sometimes
    if isinstance(outcomes_raw, str):
        import json
        try:
            outcomes_raw = json.loads(outcomes_raw)
        except Exception:
            outcomes_raw = [o.strip() for o in outcomes_raw.split(",")]

    if isinstance(prices_raw, str):
        import json
        try:
            prices_raw = json.loads(prices_raw)
        except Exception:
            prices_raw = [p.strip() for p in prices_raw.split(",")]

    if not outcomes_raw or not prices_raw:
        return None

    if binary_only and len(outcomes_raw) != 2:
        return None

    # Build odds dict — prices are already implied probabilities (0–1)
    current_odds = {}
    for outcome, price in zip(outcomes_raw, prices_raw):
        try:
            current_odds[str(outcome)] = round(float(price), 4)
        except (ValueError, TypeError):
            return None

    # Auto-generate keywords from the question text
    keywords = _extract_keywords(question)

    market_id = raw.get("id", raw.get("conditionId", "unknown"))
    slug = raw.get("slug", "")
    url = f"https://polymarket.com/event/{slug}" if slug else f"https://polymarket.com/market/{market_id}"

    return {
        "id": market_id,
        "question": question,
        "outcomes": list(outcomes_raw),
        "current_odds": current_odds,
        "keywords": keywords,
        "volume": volume,
        "end_date": raw.get("endDate", ""),
        "url": url,
    }


def _extract_keywords(question: str) -> List[str]:
    """
    Naive keyword extractor — pulls meaningful words from a market question
    to use as search terms for the data fetcher.
    Strips common question words (will, the, a, etc.) and keeps substantive ones.
    """
    stopwords = {
        "will", "the", "a", "an", "of", "in", "to", "by", "be", "is",
        "are", "was", "were", "for", "on", "at", "with", "this", "that",
        "it", "its", "or", "and", "not", "no", "yes", "have", "has",
        "had", "before", "after", "than", "more", "most", "any", "all",
        "does", "do", "did", "from", "up", "out", "end", "2024", "2025",
        "2026", "2027", "q1", "q2", "q3", "q4",
    }
    # Strip punctuation, lowercase, split
    words = re.sub(r"[^\w\s]", " ", question.lower()).split()
    keywords = [w for w in words if w not in stopwords and len(w) > 3]

    # Keep top 6 most meaningful words
    return list(dict.fromkeys(keywords))[:6]  # dict preserves order + deduplicates


def fetch_market_by_id(market_id: str) -> Dict | None:
    """Fetch a single market by its PolyMarket condition ID."""
    try:
        resp = requests.get(
            f"{GAMMA_API}/markets/{market_id}",
            timeout=10,
            headers={"User-Agent": "polymarket-sandbox-bot/1.0"},
        )
        resp.raise_for_status()
        return _parse_market(resp.json(), binary_only=False, min_volume=0)
    except Exception as exc:
        logger.error("Failed to fetch market %s: %s", market_id, exc)
        return None
