"""
trend_following.py — Strategy 2: Trend Following

Logic:
  - Keeps a rolling window of recent sentiment scores per market
  - If sentiment is consistently moving in one direction (momentum), bet with it
  - Requires momentum_threshold shift over the trend_window samples

Parameters (tunable by feedback loop):
  momentum_threshold  – minimum sentiment change to confirm trend
  trend_window        – how many past scores to track
  confidence_threshold
"""

import collections
from typing import List, Dict

from modules.sentiment import analyze
from modules.strategies.base import BaseStrategy, TradeSignal


class TrendFollowingStrategy(BaseStrategy):
    name = "trend_following"

    def __init__(self, params: Dict):
        super().__init__(params)
        # Per-market rolling history:  market_id → deque of sentiment scores
        self._history: Dict[str, collections.deque] = {}

    def evaluate(self, market: Dict, articles: List[Dict]) -> TradeSignal:
        market_id = market["id"]
        window = self.params.get("trend_window", 5)
        momentum_threshold = self.params.get("momentum_threshold", 0.08)
        threshold = self._threshold()

        # Initialize history for this market if needed
        if market_id not in self._history:
            self._history[market_id] = collections.deque(maxlen=window)

        result = analyze(articles)
        self._history[market_id].append(result.score)

        history = list(self._history[market_id])

        # Need at least 2 data points to see a trend
        if len(history) < 2:
            return TradeSignal(
                should_trade=False,
                rationale=f"[{self.name}] Insufficient history (need 2, have {len(history)})",
                raw_sentiment=result.score,
            )

        # Momentum = difference between most recent and oldest score in window
        momentum = history[-1] - history[0]

        if abs(momentum) < momentum_threshold:
            return TradeSignal(
                should_trade=False,
                rationale=(
                    f"[{self.name}] Momentum {momentum:.3f} below threshold {momentum_threshold}"
                ),
                raw_sentiment=result.score,
            )

        # Confidence based on momentum magnitude, scaled to [0, 1]
        confidence = min(abs(momentum) / 0.5, 1.0)  # 0.5 momentum → full confidence

        if confidence < threshold:
            return TradeSignal(
                should_trade=False,
                rationale=f"[{self.name}] Confidence {confidence:.2f} below threshold",
                raw_sentiment=result.score,
            )

        # Bet with the trend direction
        outcome = "Yes" if momentum > 0 else "No"
        rationale = (
            f"[{self.name}] momentum={momentum:.3f} over {len(history)} samples "
            f"confidence={confidence:.2f} → bet {outcome}"
        )
        self.logger.debug(rationale)

        return TradeSignal(
            should_trade=True,
            outcome=outcome,
            confidence=confidence,
            rationale=rationale,
            raw_sentiment=result.score,
        )
