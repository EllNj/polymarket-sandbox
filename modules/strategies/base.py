"""
base.py — Abstract base class for all trading strategies.

To add a new strategy:
  1. Create a new file in modules/strategies/
  2. Subclass BaseStrategy
  3. Implement evaluate() — return a TradeSignal
  4. Add it to modules/strategies/__init__.py ALL_STRATEGIES list

That's it. The paper trader and main loop pick it up automatically.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Dict, Optional


@dataclass
class TradeSignal:
    """
    Output from a strategy's evaluate() call.

    Fields:
      should_trade    – True if the strategy recommends placing a trade
      outcome         – Which outcome to bet on (e.g. "Yes" or "No")
      confidence      – [0.0, 1.0] how confident the strategy is
      rationale       – Human-readable explanation (logged + stored in DB)
      raw_sentiment   – The raw sentiment score used (for analytics)
    """
    should_trade: bool
    outcome: Optional[str] = None
    confidence: float = 0.0
    rationale: str = ""
    raw_sentiment: float = 0.0


class BaseStrategy(ABC):
    """
    All strategies inherit from this class.

    Each strategy receives:
      - market:   dict from DUMMY_MARKETS (question, outcomes, current_odds, keywords)
      - articles: list of fetched article dicts (from data_fetcher.fetch_all)
      - params:   strategy-specific parameter dict (from config.STRATEGY_PARAMS,
                  and updated by the feedback loop over time)
    """

    name: str = "base"  # Override in each subclass

    def __init__(self, params: Dict):
        self.params = params
        self.logger = logging.getLogger(f"strategy.{self.name}")

    def update_params(self, new_params: Dict) -> None:
        """Called by the feedback loop to adjust strategy parameters."""
        self.params.update(new_params)
        self.logger.info("Parameters updated: %s", new_params)

    @abstractmethod
    def evaluate(self, market: Dict, articles: List[Dict]) -> TradeSignal:
        """
        Analyze the market + articles and return a TradeSignal.
        This is the only method subclasses MUST implement.
        """
        ...

    def _threshold(self) -> float:
        """Convenience accessor for this strategy's confidence_threshold."""
        return self.params.get("confidence_threshold", 0.55)
