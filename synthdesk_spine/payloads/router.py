"""
Router domain event payloads.
"""

from dataclasses import dataclass
from typing import Literal, Tuple


DirectionType = Literal["long", "short", "flat"]
RiskCapType = Literal["low", "normal", "high"]
PermissionType = Literal["allow", "forbid"]


@dataclass(frozen=True)
class RouterIntentPayload:
    """
    Payload for router.intent event.

    Deterministic trading intent derived from regime events.
    """

    symbol: str
    direction: DirectionType
    size_pct: float  # >= 0
    risk_cap: RiskCapType
    rationale: Tuple[str, ...]

    def __post_init__(self) -> None:
        if isinstance(self.rationale, list):
            object.__setattr__(self, "rationale", tuple(self.rationale))
        elif not isinstance(self.rationale, tuple):
            raise TypeError("rationale must be a tuple or list of strings")
        if self.size_pct < 0:
            raise ValueError(f"size_pct must be >= 0, got {self.size_pct}")
        if not self.rationale:
            raise ValueError("rationale must be non-empty")


@dataclass(frozen=True)
class RouterPermissionPayload:
    """
    Payload for router.permission event.

    Permission gate state (allow/forbid downstream action).
    """

    permission: PermissionType
    reason: str
    since_event_id: str  # Event that triggered this permission state
