"""
sentiment.py — Sentiment analysis using VADER (default, no API needed).

VADER returns a compound score in [-1.0, +1.0]:
  +1.0 = maximally positive  →  bullish / "Yes" outcome favored
  -1.0 = maximally negative  →  bearish / "No" outcome favored

This module also contains an optional DistilBERT path (commented out by
default) which you can enable by installing transformers + torch.

Public API:
  analyze(articles)  →  SentimentResult(score, label, confidence, per_source)
"""

import logging
from dataclasses import dataclass, field
from typing import List, Dict

from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

logger = logging.getLogger(__name__)

_vader = SentimentIntensityAnalyzer()


@dataclass
class SentimentResult:
    """Output of a sentiment analysis pass."""
    score: float          # Aggregate weighted score in [-1.0, +1.0]
    label: str            # "positive" | "negative" | "neutral"
    confidence: float     # 0.0 – 1.0; distance from neutral, scaled
    per_source: Dict[str, float] = field(default_factory=dict)
    article_count: int = 0


# ---------------------------------------------------------------------------
# VADER (default — always available after `pip install vaderSentiment`)
# ---------------------------------------------------------------------------

def _score_text_vader(text: str) -> float:
    """Return compound VADER score for a single text string."""
    if not text or not text.strip():
        return 0.0
    scores = _vader.polarity_scores(text)
    return scores["compound"]  # in [-1, +1]


def analyze(articles: List[Dict], source_weights: Dict[str, float] | None = None) -> SentimentResult:
    """
    Analyze a list of article dicts (each with 'source', 'title', 'body').
    Applies per-source weights from config if provided.

    Returns a SentimentResult with an aggregate score and confidence.
    """
    if not articles:
        return SentimentResult(score=0.0, label="neutral", confidence=0.0, article_count=0)

    default_weights = {"rss": 0.30, "guardian": 0.25, "nyt": 0.20, "stocktwits": 0.15, "reddit": 0.10}
    weights = source_weights or default_weights

    # Accumulate scores per source
    source_scores: Dict[str, List[float]] = {"rss": [], "guardian": [], "nyt": [], "stocktwits": [], "reddit": []}
    for art in articles:
        text = f"{art.get('title', '')} {art.get('body', '')}"
        score = _score_text_vader(text)
        source = art.get("source", "rss")
        if source in source_scores:
            source_scores[source].append(score)

    # Average within each source, then weighted average across sources
    per_source_avg: Dict[str, float] = {}
    weighted_sum = 0.0
    weight_total = 0.0

    for src, scores in source_scores.items():
        if scores:
            avg = sum(scores) / len(scores)
            per_source_avg[src] = round(avg, 4)
            w = weights.get(src, 0.33)
            weighted_sum += avg * w
            weight_total += w

    if weight_total == 0:
        return SentimentResult(score=0.0, label="neutral", confidence=0.0, article_count=len(articles))

    aggregate = weighted_sum / weight_total

    # Map score → label
    if aggregate > 0.05:
        label = "positive"
    elif aggregate < -0.05:
        label = "negative"
    else:
        label = "neutral"

    # Confidence = how far from neutral (0) we are, scaled to [0, 1]
    # VADER compound spans [-1, +1] so abs(score) is already 0–1
    confidence = min(abs(aggregate) * 1.5, 1.0)  # stretch slightly; cap at 1.0

    logger.debug(
        "Sentiment: score=%.3f label=%s confidence=%.3f (n=%d articles, sources=%s)",
        aggregate, label, confidence, len(articles), per_source_avg,
    )

    return SentimentResult(
        score=round(aggregate, 4),
        label=label,
        confidence=round(confidence, 4),
        per_source=per_source_avg,
        article_count=len(articles),
    )


# ---------------------------------------------------------------------------
# OPTIONAL: Hugging Face DistilBERT (uncomment to use)
# Requires: pip install transformers torch
# Much more accurate but slower and uses ~250 MB RAM.
# ---------------------------------------------------------------------------

# from transformers import pipeline as hf_pipeline
# _distilbert = hf_pipeline(
#     "sentiment-analysis",
#     model="distilbert-base-uncased-finetuned-sst-2-english",
#     truncation=True,
#     max_length=512,
# )
#
# def _score_text_distilbert(text: str) -> float:
#     """Return a [-1, +1] score using DistilBERT."""
#     if not text.strip():
#         return 0.0
#     result = _distilbert(text[:512])[0]
#     score = result["score"]   # 0-1 confidence
#     return score if result["label"] == "POSITIVE" else -score
#
# def analyze_distilbert(articles: List[Dict], source_weights=None) -> SentimentResult:
#     """Drop-in replacement for analyze() using DistilBERT."""
#     # Same structure as analyze() above — swap _score_text_vader for _score_text_distilbert
#     ...
