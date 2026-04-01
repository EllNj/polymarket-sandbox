"""
data_fetcher.py — Pulls real-time articles from five free sources:
  1. RSS feeds      (no key — Reuters, BBC, Guardian, NYT, WSJ)
  2. Guardian API   (free, real-time, keyword search)
  3. NYT API        (free, real-time, keyword search, 500 req/day)
  4. StockTwits     (free, no key, finance/crypto social sentiment)
  5. Reddit JSON    (free, unauthenticated fallback — disable if rate limited)

Each fetch function returns a list of article dicts:
  {"source": str, "title": str, "body": str, "url": str}
"""

import concurrent.futures
import hashlib
import logging
import time
from typing import List, Dict

import feedparser
import requests

from config import API_KEYS, RSS_FEEDS, TRADING_CONFIG

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# RESULT CACHE — shared across all strategy threads
# Avoids making 3 identical API calls (one per bot) for the same market.
# Cache TTL matches the poll interval so data is always fresh each cycle.
# ---------------------------------------------------------------------------
import threading

_cache: Dict[str, tuple] = {}        # key → (timestamp, results)
_cache_lock = threading.Lock()
_inflight: Dict[str, threading.Event] = {}  # key → event for in-progress fetches
_inflight_lock = threading.Lock()
CACHE_TTL = 1800  # 30 minutes

# NYT allows 10 req/min — global lock ensures we never burst
_nyt_lock = threading.Lock()
_nyt_last_call = 0.0
NYT_MIN_INTERVAL = 7.0  # seconds between NYT calls (≈8/min, safely under 10/min)

# Google Trends — serialize calls to avoid 429s
_trends_lock = threading.Lock()
_trends_last_call = 0.0
TRENDS_MIN_INTERVAL = 15.0


def _cache_key(keywords: List[str]) -> str:
    return hashlib.md5("|".join(sorted(keywords)).encode()).hexdigest()


def _get_cached(keywords: List[str]) -> List[Dict] | None:
    key = _cache_key(keywords)
    with _cache_lock:
        if key in _cache:
            ts, results = _cache[key]
            if time.time() - ts < CACHE_TTL:
                logger.debug("Cache hit for keywords: %s", keywords)
                return results
    return None


def _set_cached(keywords: List[str], results: List[Dict]) -> None:
    key = _cache_key(keywords)
    with _cache_lock:
        _cache[key] = (time.time(), results)


# ---------------------------------------------------------------------------
# 1. RSS FEEDS — parallelised across all feeds simultaneously
# ---------------------------------------------------------------------------

def _fetch_single_rss(feed_url: str, keywords: List[str]) -> List[Dict]:
    """Fetch one RSS feed and return keyword-matched articles."""
    articles = []
    try:
        feed = feedparser.parse(feed_url)
        for entry in feed.entries:
            title = entry.get("title", "")
            summary = entry.get("summary", "")
            combined = f"{title} {summary}".lower()
            if any(kw.lower() in combined for kw in keywords):
                articles.append({
                    "source": "rss",
                    "title": title,
                    "body": summary,
                    "url": entry.get("link", ""),
                })
    except Exception as exc:
        logger.warning("RSS feed error (%s): %s", feed_url, exc)
    return articles


def fetch_rss(keywords: List[str], max_articles: int = 20) -> List[Dict]:
    """
    Fetch all RSS feeds in parallel (was sequential — now ~5x faster).
    No API key required.
    """
    articles = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(RSS_FEEDS)) as executor:
        futures = {executor.submit(_fetch_single_rss, url, keywords): url for url in RSS_FEEDS}
        for future in concurrent.futures.as_completed(futures):
            articles.extend(future.result())

    logger.debug("RSS: fetched %d relevant articles for keywords %s", len(articles), keywords)
    return articles[:max_articles]


# ---------------------------------------------------------------------------
# 2. GUARDIAN API — free, real-time, keyword search
#    Key: open-platform.theguardian.com/access
# ---------------------------------------------------------------------------

def fetch_guardian(keywords: List[str], max_articles: int = 10) -> List[Dict]:
    """
    Search The Guardian's full article archive by keyword.
    Free API key from https://open-platform.theguardian.com/access/
    Returns empty list gracefully if key not configured.
    """
    api_key = API_KEYS.get("guardian", "")
    if not api_key or api_key.startswith("YOUR_"):
        logger.debug("Guardian API key not configured — skipping.")
        return []

    query = " OR ".join(keywords[:5])
    url = "https://content.guardianapis.com/search"
    params = {
        "q":           query,
        "api-key":     api_key,
        "show-fields": "trailText,bodyText",
        "order-by":    "newest",
        "page-size":   max_articles,
    }

    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        results = resp.json().get("response", {}).get("results", [])
        articles = []
        for item in results:
            fields = item.get("fields", {})
            body = fields.get("trailText", "") or fields.get("bodyText", "")[:500]
            articles.append({
                "source": "guardian",
                "title": item.get("webTitle", ""),
                "body":  body,
                "url":   item.get("webUrl", ""),
            })
        logger.debug("Guardian API: fetched %d articles", len(articles))
        return articles
    except requests.RequestException as exc:
        logger.warning("Guardian API request failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# 3. NYT ARTICLE SEARCH API — free, real-time, 500 req/day
#    Key: developer.nytimes.com
# ---------------------------------------------------------------------------

def fetch_nyt(keywords: List[str], max_articles: int = 10) -> List[Dict]:
    """
    Search NYT articles by keyword using the Article Search API.
    Free key from https://developer.nytimes.com
    Returns empty list gracefully if key not configured.
    """
    api_key = API_KEYS.get("nyt", "")
    if not api_key or api_key.startswith("YOUR_"):
        logger.debug("NYT API key not configured — skipping.")
        return []

    query = " ".join(keywords[:5])
    url = "https://api.nytimes.com/svc/search/v2/articlesearch.json"
    params = {
        "q":       query,
        "api-key": api_key,
        "sort":    "newest",
        "fl":      "headline,abstract,web_url",
    }

    global _nyt_last_call
    with _nyt_lock:
        wait = NYT_MIN_INTERVAL - (time.time() - _nyt_last_call)
        if wait > 0:
            time.sleep(wait)
        _nyt_last_call = time.time()

    for attempt in range(3):
        try:
            resp = requests.get(url, params=params, timeout=10)
            if resp.status_code == 429:
                wait = 12 * (attempt + 1)  # 12s, 24s, 36s — NYT limit is 10 req/min
                logger.warning("NYT rate limited — waiting %ds (attempt %d)", wait, attempt + 1)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            docs = resp.json().get("response", {}).get("docs", [])
            articles = []
            for doc in docs[:max_articles]:
                headline = doc.get("headline", {})
                title = headline.get("main", "") if isinstance(headline, dict) else str(headline)
                articles.append({
                    "source": "nyt",
                    "title": title,
                    "body":  doc.get("abstract", ""),
                    "url":   doc.get("web_url", ""),
                })
            logger.debug("NYT API: fetched %d articles", len(articles))
            return articles
        except requests.RequestException as exc:
            logger.warning("NYT API request failed: %s", exc)
            return []
    return []


# ---------------------------------------------------------------------------
# 4. GOOGLE TRENDS — free, no key needed, works for ANY topic
#    Measures search interest (0-100) over the past 7 days.
#    Rising interest = topic gaining attention = amplified signal.
#    Falling interest = topic cooling = reduced confidence.
# ---------------------------------------------------------------------------

def fetch_google_trends(keywords: List[str], max_articles: int = 5) -> List[Dict]:  # noqa: ARG001
    """Disabled — Google aggressively rate limits concurrent requests (429). Re-enable for single-bot use."""
    return []


def _fetch_google_trends_impl(keywords: List[str], max_articles: int = 5) -> List[Dict]:
    """
    Fetch Google Trends interest for keywords over the past 7 days.
    Converts trend direction into sentiment-scored text for VADER.
    Works for any topic — finance, politics, sports, entertainment.
    No API key required.
    """
    try:
        from pytrends.request import TrendReq  # noqa: PLC0415
    except ImportError:
        logger.warning("pytrends not installed — run: pip install pytrends")
        return []

    global _trends_last_call
    with _trends_lock:
        wait = TRENDS_MIN_INTERVAL - (time.time() - _trends_last_call)
        if wait > 0:
            time.sleep(wait)
        _trends_last_call = time.time()

    try:
        pt = TrendReq(hl="en-US", tz=0, timeout=(10, 25))
        # Use top 5 keywords, Google Trends max is 5 per request
        kws = keywords[:5]
        pt.build_payload(kws, timeframe="now 7-d")
        df = pt.interest_over_time()

        if df.empty:
            logger.debug("Google Trends: no data for keywords %s", kws)
            return []

        articles = []
        for kw in kws:
            if kw not in df.columns:
                continue

            series = df[kw]
            current = series.iloc[-1]
            week_avg = series.mean()
            recent_avg = series.iloc[-len(series)//3:].mean()  # Last third of week
            early_avg = series.iloc[:len(series)//3].mean()    # First third of week

            # Determine trend direction
            if recent_avg > early_avg * 1.15:
                direction = "surging rapidly increasing significantly"
                trend = "rising"
            elif recent_avg < early_avg * 0.85:
                direction = "declining falling dropping significantly"
                trend = "falling"
            else:
                direction = "stable steady unchanged"
                trend = "flat"

            # Build text VADER can score — rising = positive, falling = negative
            body = (
                f"Search interest for {kw} is {direction}. "
                f"Current interest level is {current:.0f} out of 100, "
                f"weekly average {week_avg:.0f}. Trend is {trend}."
            )

            articles.append({
                "source": "google_trends",
                "title": f"Google Trends: '{kw}' interest is {trend} ({current:.0f}/100)",
                "body":  body,
                "url":   "",
            })

        logger.debug("Google Trends: %d keyword signals fetched", len(articles))
        return articles

    except Exception as exc:
        logger.warning("Google Trends error: %s", exc)
        return []


# ---------------------------------------------------------------------------
# 5. STOCKTWITS — free, no key needed, finance/crypto social sentiment
#    Maps market keywords to relevant ticker symbols automatically.
# ---------------------------------------------------------------------------

# Keyword → StockTwits symbol mapping
# StockTwits is symbol-based so we map topics to relevant tickers
STOCKTWITS_SYMBOL_MAP = {
    "bitcoin":         ["BTC.X"],
    "btc":             ["BTC.X"],
    "crypto":          ["BTC.X", "ETH.X"],
    "ethereum":        ["ETH.X"],
    "federal reserve": ["SPY", "TLT", "GLD"],
    "fed":             ["SPY", "TLT"],
    "interest rates":  ["TLT", "SPY"],
    "inflation":       ["TLT", "GLD", "SPY"],
    "s&p":             ["SPY", "QQQ"],
    "nasdaq":          ["QQQ"],
    "stock market":    ["SPY", "QQQ"],
    "tesla":           ["TSLA"],
    "elon musk":       ["TSLA"],
    "apple":           ["AAPL"],
    "microsoft":       ["MSFT"],
    "openai":          ["MSFT"],
    "google":          ["GOOGL"],
    "oil":             ["USO", "XOM"],
    "gold":            ["GLD"],
    "recession":       ["SPY", "TLT"],
    "unemployment":    ["SPY", "TLT"],
    "election":        ["SPY", "TLT"],
    "trump":           ["SPY", "TLT"],
    "tariff":          ["SPY", "EEM"],
    "trade":           ["SPY", "EEM"],
}


def _keywords_to_symbols(keywords: List[str]) -> List[str]:
    """Map a list of keywords to relevant StockTwits ticker symbols."""
    symbols = []
    for kw in keywords:
        kw_lower = kw.lower()
        for map_key, map_symbols in STOCKTWITS_SYMBOL_MAP.items():
            if map_key in kw_lower or kw_lower in map_key:
                symbols.extend(map_symbols)
    # Deduplicate, keep up to 3 symbols
    return list(dict.fromkeys(symbols))[:3]


def fetch_stocktwits(keywords: List[str], max_posts: int = 15) -> List[Dict]:  # noqa: ARG001
    """StockTwits now requires auth (403) — disabled until they restore free access."""
    return []


def _fetch_stocktwits_impl(keywords: List[str], max_posts: int = 15) -> List[Dict]:
    """
    Fetch recent posts from StockTwits for symbols related to the keywords.
    No API key required. Rate limit: ~200 requests/hour unauthenticated.
    Falls back to trending stream if no symbol match found.
    """
    symbols = _keywords_to_symbols(keywords)
    articles = []
    headers = {"User-Agent": "polymarket_sandbox_bot/1.0"}

    if symbols:
        for symbol in symbols:
            url = f"https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json"
            try:
                resp = requests.get(url, headers=headers, timeout=10)
                if resp.status_code == 429:
                    logger.warning("StockTwits rate limited — skipping symbol %s", symbol)
                    continue
                resp.raise_for_status()
                messages = resp.json().get("messages", [])
                for msg in messages[:max_posts // len(symbols)]:
                    body = msg.get("body", "")
                    # Filter by keyword relevance
                    if any(kw.lower() in body.lower() for kw in keywords) or True:
                        articles.append({
                            "source": "stocktwits",
                            "title": f"${symbol}: {body[:80]}",
                            "body":  body,
                            "url":   f"https://stocktwits.com/message/{msg.get('id', '')}",
                        })
            except Exception as exc:
                logger.warning("StockTwits error (symbol=%s): %s", symbol, exc)
            time.sleep(0.5)  # Courtesy delay between symbol requests
    else:
        # No symbol match — fetch trending and filter by keywords
        try:
            resp = requests.get(
                "https://api.stocktwits.com/api/2/streams/trending.json",
                headers=headers, timeout=10,
            )
            resp.raise_for_status()
            messages = resp.json().get("messages", [])
            for msg in messages:
                body = msg.get("body", "")
                if any(kw.lower() in body.lower() for kw in keywords):
                    articles.append({
                        "source": "stocktwits",
                        "title": body[:80],
                        "body":  body,
                        "url":   f"https://stocktwits.com/message/{msg.get('id', '')}",
                    })
        except Exception as exc:
            logger.warning("StockTwits trending error: %s", exc)

    logger.debug("StockTwits: fetched %d posts for keywords %s", len(articles), keywords)
    return articles[:max_posts]


# ---------------------------------------------------------------------------
# 5. REDDIT JSON — unauthenticated fallback (disable if rate limited)
# ---------------------------------------------------------------------------

def _fetch_reddit_json(keywords: List[str], max_posts: int) -> List[Dict]:
    """
    Unauthenticated Reddit JSON fallback.
    Set TRADING_CONFIG["reddit_enabled"] = False in config.py to skip.
    Retries once on 429 with backoff.
    """
    if not TRADING_CONFIG.get("reddit_enabled", False):
        logger.debug("Reddit disabled in config — skipping")
        return []

    articles = []
    query = " ".join(keywords[:3])
    headers = {"User-Agent": "polymarket_sandbox_bot/1.0"}

    for sub in ["news", "worldnews"]:
        url = f"https://www.reddit.com/r/{sub}/search.json"
        params = {"q": query, "sort": "new", "limit": max_posts // 2, "t": "week"}
        for attempt in range(2):
            try:
                resp = requests.get(url, params=params, headers=headers, timeout=10)
                if resp.status_code == 429:
                    wait = 10 * (attempt + 1)
                    logger.warning("Reddit rate limited (r/%s) — waiting %ds", sub, wait)
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                for post in resp.json().get("data", {}).get("children", []):
                    d = post.get("data", {})
                    combined = f"{d.get('title','')} {d.get('selftext','')}".lower()
                    if any(kw.lower() in combined for kw in keywords):
                        articles.append({
                            "source": "reddit",
                            "title": d.get("title", ""),
                            "body":  d.get("selftext", "")[:500],
                            "url":   d.get("url", ""),
                        })
                break
            except Exception as exc:
                logger.warning("Reddit JSON error (r/%s): %s", sub, exc)
                break
        time.sleep(2)

    logger.debug("Reddit (JSON): fetched %d posts", len(articles))
    return articles[:max_posts]


# ---------------------------------------------------------------------------
# COMBINED FETCH — all sources in parallel
# ---------------------------------------------------------------------------

def fetch_all(keywords: List[str], max_per_source: int = 15) -> List[Dict]:
    """
    Fetch from all sources concurrently and aggregate results.
    Results are cached for CACHE_TTL seconds.

    Race condition fix: if two bots request the same keywords simultaneously,
    the second waits for the first to finish rather than making duplicate API calls.
    """
    key = _cache_key(keywords)

    # Fast path — cache hit
    cached = _get_cached(keywords)
    if cached is not None:
        return cached

    # Check if another thread is already fetching these keywords
    with _inflight_lock:
        if key in _inflight:
            event = _inflight[key]
            wait_for_other = True
        else:
            event = threading.Event()
            _inflight[key] = event
            wait_for_other = False

    if wait_for_other:
        # Wait up to 60s for the in-progress fetch to complete, then use cache
        event.wait(timeout=60)
        return _get_cached(keywords) or []

    # We are the designated fetcher for this key
    results = []
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        futures = [
            executor.submit(fetch_rss,           keywords, max_per_source),
            executor.submit(fetch_guardian,      keywords, max_per_source),
            executor.submit(fetch_nyt,           keywords, max_per_source),
            executor.submit(fetch_stocktwits,    keywords, max_per_source),
            executor.submit(fetch_google_trends, keywords, max_per_source),
            executor.submit(_fetch_reddit_json,  keywords, max_per_source),
        ]
            for future in concurrent.futures.as_completed(futures):
                try:
                    results.extend(future.result())
                except Exception as exc:
                    logger.warning("fetch_all: source error: %s", exc)

        if not results:
            logger.warning("No articles found for keywords: %s", keywords)

        _set_cached(keywords, results)
        return results
    finally:
        # Signal any waiting threads and remove from in-flight tracker
        with _inflight_lock:
            _inflight.pop(key, None)
        event.set()
