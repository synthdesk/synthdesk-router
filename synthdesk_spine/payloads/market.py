"""
Market domain event payloads.
"""

from dataclasses import dataclass
from typing import Literal


RegimeType = Literal["chop", "drift_up", "drift_down", "high_vol"]
WindowType = Literal["30s", "5m", "1h"]
VolBucketType = Literal["low", "mid", "high"]


@dataclass(frozen=True)
class MarketRegimePayload:
    """
    Payload for market.regime event.

    Represents current regime classification for a symbol.
    """

    symbol: str
    regime: RegimeType
    confidence: float  # 0.0–1.0
    window: WindowType
    vol_bucket: VolBucketType

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be in [0, 1], got {self.confidence}")


@dataclass(frozen=True)
class MarketRegimeChangePayload:
    """
    Payload for market.regime_change event.

    Emitted when symbol transitions between regimes.
    """

    symbol: str
    from_regime: RegimeType
    to_regime: RegimeType
    confidence: float  # 0.0–1.0
    window: WindowType

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be in [0, 1], got {self.confidence}")
        if self.from_regime == self.to_regime:
            raise ValueError("from_regime and to_regime must differ")
