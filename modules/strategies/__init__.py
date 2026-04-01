# strategies package
from .sentiment_weighted import SentimentWeightedStrategy
from .trend_following import TrendFollowingStrategy
from .contrarian import ContrarianStrategy

ALL_STRATEGIES = [
    SentimentWeightedStrategy,
    TrendFollowingStrategy,
    ContrarianStrategy,
]
