"""
contrarian.py — Strategy 3: Contrarian

Logic:
  - Only activates when sentiment is extreme (above reversal_threshold)
  - Bets AGAINST the prevailing sentiment — assumes the market has over-reacted
  - Inspired by mean-reversion / "sell the news" dynamics

Parameters (tunable by feedback loop):
  reversal_threshold    – how extreme sentiment must be to trigger a contrarian bet
  contrarian_strength   – multiplier applied to confidence when going contrarian
  confidence_threshold
"""

from typing import List, Dict

from modules.sentiment import analyze
from modules.strategies.base import BaseStrategy, TradeSignal


class ContrarianStrategy(BaseStrategy):
    name = "contrarian"

    def evaluate(self, market: Dict, articles: List[Dict]) -> TradeSignal:
        reversal_threshold = self.params.get("reversal_threshold", 0.75)
        contrarian_strength = self.params.get("contrarian_strength", 0.85)
        threshold = self._threshold()

        result = analyze(articles)

        # Only trade when sentiment is extreme enough to bet against
        if result.confidence < reversal_threshold:
            return TradeSignal(
                should_trade=False,
                rationale=(
                    f"[{self.name}] Sentiment not extreme enough: "
                    f"confidence={result.confidence:.2f} < reversal_threshold={reversal_threshold}"
                ),
                raw_sentiment=result.score,
            )

        # Go contrarian — bet opposite to the sentiment label
        if result.label == "positive":
            outcome = "No"   # Crowd is too optimistic → bet No
        elif result.label == "negative":
            outcome = "Yes"  # Crowd is too pessimistic → bet Yes
        else:
            return TradeSignal(
                should_trade=False,
                rationale=f"[{self.name}] Neutral — contrarian has no edge",
                raw_sentiment=result.score,
            )

        # Confidence is scaled down by contrarian_strength (uncertain bet)
        confidence = result.confidence * contrarian_strength

        if confidence < threshold:
            return TradeSignal(
                should_trade=False,
                rationale=(
                    f"[{self.name}] Contrarian confidence {confidence:.2f} below threshold {threshold}"
                ),
                raw_sentiment=result.score,
            )

        rationale = (
            f"[{self.name}] Extreme {result.label} sentiment ({result.score:.3f}) → "
            f"contrarian bet {outcome}, confidence={confidence:.2f}"
        )
        self.logger.debug(rationale)

        return TradeSignal(
            should_trade=True,
            outcome=outcome,
            confidence=confidence,
            rationale=rationale,
            raw_sentiment=result.score,
        )
