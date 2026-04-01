"""
sentiment_weighted.py — Strategy 1: Sentiment-Weighted

Logic:
  - Fetch articles for this market's keywords
  - Run VADER sentiment analysis with per-source weights
  - If aggregate sentiment is clearly positive → bet "Yes"
  - If aggregate sentiment is clearly negative → bet "No"
  - Confidence = scaled sentiment magnitude

Parameters (tunable by feedback loop):
  source_weights        – how much to trust rss vs newsapi vs reddit
  confidence_threshold  – minimum confidence before placing a trade
  lookback_articles     – how many articles to feed into the analyzer
"""

from typing import List, Dict

from modules.sentiment import analyze
from modules.strategies.base import BaseStrategy, TradeSignal


class SentimentWeightedStrategy(BaseStrategy):
    name = "sentiment_weighted"

    def evaluate(self, market: Dict, articles: List[Dict]) -> TradeSignal:
        lookback = self.params.get("lookback_articles", 10)
        source_weights = self.params.get("source_weights", {})

        trimmed = articles[:lookback]
        result = analyze(trimmed, source_weights=source_weights)

        threshold = self._threshold()

        # Not enough signal — skip the trade
        if result.confidence < threshold:
            return TradeSignal(
                should_trade=False,
                rationale=f"Confidence {result.confidence:.2f} below threshold {threshold:.2f}",
                raw_sentiment=result.score,
            )

        # Map sentiment direction to an outcome
        if result.label == "positive":
            outcome = "Yes"
        elif result.label == "negative":
            outcome = "No"
        else:
            return TradeSignal(
                should_trade=False,
                rationale=f"Neutral sentiment (score={result.score:.3f}), no edge",
                raw_sentiment=result.score,
            )

        rationale = (
            f"[{self.name}] sentiment={result.label} score={result.score:.3f} "
            f"confidence={result.confidence:.2f} articles={result.article_count} "
            f"per_source={result.per_source}"
        )
        self.logger.debug(rationale)

        return TradeSignal(
            should_trade=True,
            outcome=outcome,
            confidence=result.confidence,
            rationale=rationale,
            raw_sentiment=result.score,
        )
