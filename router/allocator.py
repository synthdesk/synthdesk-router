"""
Uncertainty Allocator - Epistemic Posture Engine

Converts regime state + entropy into always-allocated capital posture.

Principles:
- No directional belief (regime-reactive, not predictive)
- No execution power (symbolic allocation only)
- No leverage (1x maximum)
- Uncertainty-encoded sizing (less certain → smaller allocation)

This is the economic core of v0.2 authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Tuple


# ---------------------------
# Constants
# ---------------------------

SIZE_PCT_SCALE = 10000  # Quantization denominator


class Regime(Enum):
    """Market regime classifications."""

    CHOP = "chop"           # No edge, flat posture
    HIGH_VOL = "high_vol"   # Risk excess, reduced exposure
    DRIFT = "drift"         # Trend present, directional posture
    BREAKOUT = "breakout"   # Momentum event, elevated exposure
    UNKNOWN = "unknown"     # Unclassified, veto


class RiskCap(Enum):
    """Maximum acceptable risk tier for v0.2."""

    ZERO = "zero"       # No risk tolerance
    LOW = "low"         # Conservative
    MEDIUM = "medium"   # Moderate

    # NOT permitted at v0.2:
    # HIGH = "high"
    # UNLIMITED = "unlimited"


class Direction(Enum):
    """Exposure polarity."""

    FLAT = "flat"
    LONG = "long"
    SHORT = "short"


# ---------------------------
# Regime → Posture Mapping
# ---------------------------

@dataclass(frozen=True)
class RegimePosture:
    """
    Posture specification for a given regime.

    Defines the exposure characteristics appropriate for each regime,
    without any directional belief.
    """

    direction: Direction
    base_allocation_q: int      # Base allocation (quantized)
    uncertainty_discount: float  # [0, 1] - applied to base
    risk_cap: RiskCap
    rationale: str


# The regime → posture map encodes epistemic policy, not prediction
REGIME_POSTURE_MAP: Dict[Regime, RegimePosture] = {
    Regime.CHOP: RegimePosture(
        direction=Direction.FLAT,
        base_allocation_q=0,
        uncertainty_discount=1.0,
        risk_cap=RiskCap.ZERO,
        rationale="regime=chop: no edge, flat posture",
    ),
    Regime.HIGH_VOL: RegimePosture(
        direction=Direction.FLAT,
        base_allocation_q=0,
        uncertainty_discount=1.0,
        risk_cap=RiskCap.ZERO,
        rationale="regime=high_vol: risk excess, no exposure",
    ),
    Regime.DRIFT: RegimePosture(
        direction=Direction.LONG,  # Drift implies trend-following
        base_allocation_q=2500,    # 25% base allocation
        uncertainty_discount=0.8,  # 20% uncertainty discount
        risk_cap=RiskCap.LOW,
        rationale="regime=drift: trend present, conservative long",
    ),
    Regime.BREAKOUT: RegimePosture(
        direction=Direction.LONG,  # Breakout implies momentum
        base_allocation_q=5000,    # 50% base allocation
        uncertainty_discount=0.6,  # 40% uncertainty discount (high entropy)
        risk_cap=RiskCap.MEDIUM,
        rationale="regime=breakout: momentum event, moderate exposure",
    ),
    Regime.UNKNOWN: RegimePosture(
        direction=Direction.FLAT,
        base_allocation_q=0,
        uncertainty_discount=1.0,
        risk_cap=RiskCap.ZERO,
        rationale="regime=unknown: unclassified, no exposure",
    ),
}


# ---------------------------
# Entropy Model
# ---------------------------

@dataclass(frozen=True)
class EntropyState:
    """
    Entropy metrics that modulate allocation.

    Higher entropy = lower confidence = smaller allocation.
    """

    regime_confidence: float     # [0, 1] - how certain is regime classification
    regime_age_seconds: float    # Time since last regime update
    transition_proximity: float  # [0, 1] - likelihood of regime change

    @property
    def combined_entropy(self) -> float:
        """
        Compute combined entropy factor.

        Returns value in [0, 1] where:
        - 0 = maximum entropy (no allocation)
        - 1 = minimum entropy (full base allocation)
        """
        # Confidence contributes directly
        conf_factor = self.regime_confidence

        # Stale regimes increase entropy
        staleness_factor = max(0.0, 1.0 - (self.regime_age_seconds / 3600))  # Decay over 1 hour

        # Transition proximity increases entropy
        stability_factor = 1.0 - self.transition_proximity

        # Combine multiplicatively (conservative)
        return conf_factor * staleness_factor * stability_factor


def default_entropy() -> EntropyState:
    """Default entropy state when no entropy data available."""
    return EntropyState(
        regime_confidence=0.5,      # Moderate uncertainty
        regime_age_seconds=0.0,     # Fresh
        transition_proximity=0.3,   # Some instability assumed
    )


# ---------------------------
# Allocation Engine
# ---------------------------

@dataclass(frozen=True)
class AllocationResult:
    """
    Result of uncertainty-aware allocation.

    This is the output of the epistemic posture engine.
    """

    direction: Direction
    size_pct_q: int           # Quantized allocation [0, SIZE_PCT_SCALE]
    size_pct_scale: int       # Always SIZE_PCT_SCALE
    risk_cap: RiskCap
    rationale: List[str]

    # Allocation breakdown (for transparency)
    base_allocation_q: int
    entropy_factor: float
    uncertainty_discount: float
    final_factor: float

    @property
    def size_pct_display(self) -> str:
        """Human-readable size percentage."""
        pct = (self.size_pct_q / self.size_pct_scale) * 100
        return f"{pct:.2f}% (q)"

    def to_intent_fields(self) -> Dict:
        """Extract fields for router.intent event."""
        return {
            "direction": self.direction.value,
            "size_pct_q": self.size_pct_q,
            "size_pct_scale": self.size_pct_scale,
            "risk_cap": self.risk_cap.value,
            "rationale": self.rationale,
        }


def allocate(
    regime: Regime,
    entropy: Optional[EntropyState] = None,
    max_allocation_q: int = SIZE_PCT_SCALE,
) -> AllocationResult:
    """
    Compute uncertainty-aware allocation for given regime.

    This is the core of the epistemic posture engine.

    Args:
        regime: Current market regime
        entropy: Entropy state (defaults to moderate uncertainty)
        max_allocation_q: Maximum permitted allocation (safety bound)

    Returns:
        AllocationResult with direction, sizing, and rationale
    """
    if entropy is None:
        entropy = default_entropy()

    # Get base posture for regime
    posture = REGIME_POSTURE_MAP.get(regime, REGIME_POSTURE_MAP[Regime.UNKNOWN])

    # Compute entropy factor
    entropy_factor = entropy.combined_entropy

    # Apply uncertainty discount from regime
    uncertainty_discount = posture.uncertainty_discount

    # Combined factor
    final_factor = entropy_factor * uncertainty_discount

    # Compute allocation
    raw_allocation = int(posture.base_allocation_q * final_factor + 0.5)

    # Apply safety bound
    size_pct_q = min(raw_allocation, max_allocation_q)

    # Ensure non-negative
    size_pct_q = max(0, size_pct_q)

    # Build rationale
    rationale = [
        posture.rationale,
        f"entropy_factor={entropy_factor:.2f}",
        f"final_allocation={size_pct_q}/{SIZE_PCT_SCALE}",
    ]

    # If flat, always zero allocation
    if posture.direction == Direction.FLAT:
        size_pct_q = 0

    return AllocationResult(
        direction=posture.direction,
        size_pct_q=size_pct_q,
        size_pct_scale=SIZE_PCT_SCALE,
        risk_cap=posture.risk_cap,
        rationale=rationale,
        base_allocation_q=posture.base_allocation_q,
        entropy_factor=entropy_factor,
        uncertainty_discount=uncertainty_discount,
        final_factor=final_factor,
    )


# ---------------------------
# Regime Inference
# ---------------------------

def infer_regime(regime_str: Optional[str]) -> Regime:
    """
    Convert regime string to Regime enum.

    Args:
        regime_str: Regime string from spine event

    Returns:
        Regime enum value
    """
    if not regime_str:
        return Regime.UNKNOWN

    regime_lower = regime_str.lower()

    if regime_lower in ("chop", "ranging", "sideways"):
        return Regime.CHOP
    elif regime_lower in ("high_vol", "volatile", "high_volatility"):
        return Regime.HIGH_VOL
    elif regime_lower in ("drift", "trend", "trending"):
        return Regime.DRIFT
    elif regime_lower in ("breakout", "momentum", "break"):
        return Regime.BREAKOUT
    else:
        return Regime.UNKNOWN


# ---------------------------
# Integration Helper
# ---------------------------

def compute_allocation_from_state(
    state_dict: Dict,
    symbol: str,
) -> Tuple[AllocationResult, Optional[str]]:
    """
    Compute allocation from router state.

    This is the bridge between RouterState and the allocator.

    Args:
        state_dict: Router state dict with system and symbols
        symbol: Symbol to allocate for

    Returns:
        (AllocationResult, veto_reason) - veto_reason is None if intent, str if veto
    """
    system = state_dict.get("system", {})
    symbols = state_dict.get("symbols", {})
    symbol_state = symbols.get(symbol, {})

    # Check veto conditions first
    if not system.get("listener_alive", False):
        return (
            AllocationResult(
                direction=Direction.FLAT,
                size_pct_q=0,
                size_pct_scale=SIZE_PCT_SCALE,
                risk_cap=RiskCap.ZERO,
                rationale=["veto: input_unavailable"],
                base_allocation_q=0,
                entropy_factor=0.0,
                uncertainty_discount=1.0,
                final_factor=0.0,
            ),
            "input_unavailable",
        )

    if system.get("violation_active", False):
        return (
            AllocationResult(
                direction=Direction.FLAT,
                size_pct_q=0,
                size_pct_scale=SIZE_PCT_SCALE,
                risk_cap=RiskCap.ZERO,
                rationale=["veto: violation_active"],
                base_allocation_q=0,
                entropy_factor=0.0,
                uncertainty_discount=1.0,
                final_factor=0.0,
            ),
            "violation_active",
        )

    # Get regime
    regime_str = symbol_state.get("regime")
    if not regime_str:
        return (
            AllocationResult(
                direction=Direction.FLAT,
                size_pct_q=0,
                size_pct_scale=SIZE_PCT_SCALE,
                risk_cap=RiskCap.ZERO,
                rationale=["veto: regime_unresolved"],
                base_allocation_q=0,
                entropy_factor=0.0,
                uncertainty_discount=1.0,
                final_factor=0.0,
            ),
            "regime_unresolved",
        )

    regime = infer_regime(regime_str)

    # Regime-based vetoes
    if regime == Regime.CHOP:
        return (
            AllocationResult(
                direction=Direction.FLAT,
                size_pct_q=0,
                size_pct_scale=SIZE_PCT_SCALE,
                risk_cap=RiskCap.ZERO,
                rationale=["veto: regime=chop"],
                base_allocation_q=0,
                entropy_factor=0.0,
                uncertainty_discount=1.0,
                final_factor=0.0,
            ),
            "regime_chop",
        )

    if regime == Regime.HIGH_VOL:
        return (
            AllocationResult(
                direction=Direction.FLAT,
                size_pct_q=0,
                size_pct_scale=SIZE_PCT_SCALE,
                risk_cap=RiskCap.ZERO,
                rationale=["veto: regime=high_vol"],
                base_allocation_q=0,
                entropy_factor=0.0,
                uncertainty_discount=1.0,
                final_factor=0.0,
            ),
            "regime_high_vol",
        )

    # Compute allocation for intent-producing regimes
    # TODO: Derive entropy from actual state when available
    entropy = default_entropy()

    allocation = allocate(regime, entropy)

    return (allocation, None)
