"""
Portfolio Allocator - Cross-asset capital allocation with correlation limits.

Aggregates per-symbol allocations into portfolio-level positions with:
- Total exposure cap (prevent over-leveraged portfolios)
- Correlation penalty (reduce exposure to correlated assets)
- Per-asset limits (constitutional bounds)

This is the integration layer between per-symbol allocators and execution.

Constitutional constraint: no leverage (total exposure <= 100%)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

from router.allocator import AllocationResult, Direction, RiskCap, SIZE_PCT_SCALE


class CorrelationBucket(str, Enum):
    """Correlation strength buckets for penalty calculation."""

    LOW = "low"        # |corr| < 0.3: independent, no penalty
    MEDIUM = "medium"  # 0.3 <= |corr| < 0.7: moderate penalty
    HIGH = "high"      # |corr| >= 0.7: high penalty (near-duplicate exposure)


# Correlation bucket thresholds
CORRELATION_LOW_THRESHOLD = 0.3
CORRELATION_HIGH_THRESHOLD = 0.7

# Penalty factors per bucket (multiplied against secondary asset allocation)
CORRELATION_PENALTY_FACTORS = {
    CorrelationBucket.LOW: 1.0,     # No penalty
    CorrelationBucket.MEDIUM: 0.7,  # 30% reduction
    CorrelationBucket.HIGH: 0.4,    # 60% reduction
}

# Maximum total portfolio exposure (constitutional: no leverage)
MAX_TOTAL_EXPOSURE_Q = SIZE_PCT_SCALE  # 100%


@dataclass(frozen=True)
class SymbolAllocation:
    """
    Per-symbol allocation with portfolio context.

    Extends AllocationResult with portfolio-level adjustments.
    """

    symbol: str
    original: AllocationResult
    adjusted_size_q: int  # After correlation penalty
    correlation_penalty: float  # 0.0-1.0 penalty applied
    is_anchor: bool  # True if this is the anchor asset (no penalty)
    rationale: List[str] = field(default_factory=list)

    @property
    def direction(self) -> Direction:
        return self.original.direction

    @property
    def risk_cap(self) -> RiskCap:
        return self.original.risk_cap


@dataclass(frozen=True)
class PortfolioAllocation:
    """
    Cross-asset portfolio allocation result.

    Contains per-symbol allocations and portfolio-level metadata.
    """

    allocations: Dict[str, SymbolAllocation]
    total_exposure_q: int
    total_exposure_pct: float
    correlation_penalties_applied: int  # Count of penalties > 0
    exposure_cap_applied: bool  # True if exposure cap reduced allocations
    portfolio_risk_cap: RiskCap
    rationale: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        """Serialize for event payload."""
        return {
            "allocations": {
                symbol: {
                    "direction": alloc.direction.value,
                    "original_size_q": alloc.original.size_pct_q,
                    "adjusted_size_q": alloc.adjusted_size_q,
                    "correlation_penalty": alloc.correlation_penalty,
                    "is_anchor": alloc.is_anchor,
                    "risk_cap": alloc.risk_cap.value,
                }
                for symbol, alloc in self.allocations.items()
            },
            "total_exposure_q": self.total_exposure_q,
            "total_exposure_pct": self.total_exposure_pct,
            "correlation_penalties_applied": self.correlation_penalties_applied,
            "exposure_cap_applied": self.exposure_cap_applied,
            "portfolio_risk_cap": self.portfolio_risk_cap.value,
            "rationale": self.rationale,
        }


def get_correlation_bucket(correlation: float) -> CorrelationBucket:
    """
    Map correlation value to bucket.

    Uses absolute correlation (both positive and negative correlations
    reduce diversification benefit).
    """
    abs_corr = abs(correlation)
    if abs_corr < CORRELATION_LOW_THRESHOLD:
        return CorrelationBucket.LOW
    elif abs_corr < CORRELATION_HIGH_THRESHOLD:
        return CorrelationBucket.MEDIUM
    else:
        return CorrelationBucket.HIGH


def portfolio_allocate(
    symbol_allocations: Dict[str, AllocationResult],
    correlation_matrix: Optional[Dict[Tuple[str, str], float]] = None,
    anchor_symbol: Optional[str] = None,
    max_total_exposure_q: int = MAX_TOTAL_EXPOSURE_Q,
) -> PortfolioAllocation:
    """
    Aggregate per-symbol allocations with correlation penalties.

    Algorithm:
    1. Sort symbols by allocation size (largest first = anchor if not specified)
    2. Apply correlation penalties to non-anchor symbols
    3. Scale down if total exposure exceeds cap
    4. Aggregate risk cap (highest individual risk cap)

    Args:
        symbol_allocations: Per-symbol AllocationResult from constraint evaluation
        correlation_matrix: Pairwise correlations {(sym1, sym2): corr}
                          If None, no correlation penalties applied
        anchor_symbol: Symbol with no penalty (defaults to largest allocation)
        max_total_exposure_q: Maximum portfolio exposure (default 100%)

    Returns:
        PortfolioAllocation with adjusted per-symbol allocations
    """
    if not symbol_allocations:
        return PortfolioAllocation(
            allocations={},
            total_exposure_q=0,
            total_exposure_pct=0.0,
            correlation_penalties_applied=0,
            exposure_cap_applied=False,
            portfolio_risk_cap=RiskCap.ZERO,
            rationale=["empty_portfolio"],
        )

    # Sort by allocation size (descending) to determine anchor
    sorted_symbols = sorted(
        symbol_allocations.keys(),
        key=lambda s: symbol_allocations[s].size_pct_q,
        reverse=True,
    )

    # Determine anchor (largest position, or specified)
    if anchor_symbol is None or anchor_symbol not in symbol_allocations:
        anchor_symbol = sorted_symbols[0]

    # Build correlation lookup (symmetric)
    corr_lookup: Dict[Tuple[str, str], float] = {}
    if correlation_matrix:
        for (s1, s2), corr in correlation_matrix.items():
            corr_lookup[(s1, s2)] = corr
            corr_lookup[(s2, s1)] = corr

    # Apply correlation penalties
    adjusted_allocations: Dict[str, SymbolAllocation] = {}
    penalties_applied = 0
    rationale_items: List[str] = []

    for symbol in sorted_symbols:
        original = symbol_allocations[symbol]

        # Skip flat allocations
        if original.direction == Direction.FLAT or original.size_pct_q == 0:
            adjusted_allocations[symbol] = SymbolAllocation(
                symbol=symbol,
                original=original,
                adjusted_size_q=0,
                correlation_penalty=0.0,
                is_anchor=symbol == anchor_symbol,
                rationale=["flat_allocation"],
            )
            continue

        if symbol == anchor_symbol:
            # Anchor: no penalty
            adjusted_allocations[symbol] = SymbolAllocation(
                symbol=symbol,
                original=original,
                adjusted_size_q=original.size_pct_q,
                correlation_penalty=0.0,
                is_anchor=True,
                rationale=["anchor_no_penalty"],
            )
        else:
            # Non-anchor: apply correlation penalty vs anchor
            corr = corr_lookup.get((symbol, anchor_symbol), 0.0)
            bucket = get_correlation_bucket(corr)
            penalty_factor = CORRELATION_PENALTY_FACTORS[bucket]

            # Check direction alignment (same direction = correlated exposure)
            anchor_dir = symbol_allocations[anchor_symbol].direction
            if original.direction == anchor_dir and bucket != CorrelationBucket.LOW:
                # Same direction + correlated = apply penalty
                adjusted_size = int(original.size_pct_q * penalty_factor + 0.5)
                correlation_penalty = 1.0 - penalty_factor
                penalties_applied += 1
                penalty_rationale = (
                    f"corr_penalty:{bucket.value},"
                    f"factor={penalty_factor:.1f},"
                    f"corr={corr:.2f}"
                )
            else:
                # Opposite direction or low correlation = no penalty (hedging)
                adjusted_size = original.size_pct_q
                correlation_penalty = 0.0
                penalty_rationale = "no_penalty:diversified_or_hedge"

            adjusted_allocations[symbol] = SymbolAllocation(
                symbol=symbol,
                original=original,
                adjusted_size_q=adjusted_size,
                correlation_penalty=correlation_penalty,
                is_anchor=False,
                rationale=[penalty_rationale],
            )

    # Calculate total exposure
    total_exposure_q = sum(
        alloc.adjusted_size_q
        for alloc in adjusted_allocations.values()
    )

    # Apply exposure cap if needed
    exposure_cap_applied = False
    if total_exposure_q > max_total_exposure_q:
        scale_factor = max_total_exposure_q / total_exposure_q
        exposure_cap_applied = True
        rationale_items.append(
            f"exposure_cap:scale={scale_factor:.2f},"
            f"before={total_exposure_q},max={max_total_exposure_q}"
        )

        # Scale down all allocations proportionally
        new_allocations: Dict[str, SymbolAllocation] = {}
        for symbol, alloc in adjusted_allocations.items():
            new_size = int(alloc.adjusted_size_q * scale_factor + 0.5)
            new_allocations[symbol] = SymbolAllocation(
                symbol=alloc.symbol,
                original=alloc.original,
                adjusted_size_q=new_size,
                correlation_penalty=alloc.correlation_penalty,
                is_anchor=alloc.is_anchor,
                rationale=alloc.rationale + [f"scaled:{scale_factor:.2f}"],
            )
        adjusted_allocations = new_allocations
        total_exposure_q = max_total_exposure_q

    # Determine portfolio risk cap (highest individual)
    risk_cap_order = [RiskCap.ZERO, RiskCap.LOW, RiskCap.MEDIUM]
    portfolio_risk_cap = RiskCap.ZERO
    for alloc in adjusted_allocations.values():
        if alloc.adjusted_size_q > 0:
            alloc_idx = risk_cap_order.index(alloc.risk_cap)
            port_idx = risk_cap_order.index(portfolio_risk_cap)
            if alloc_idx > port_idx:
                portfolio_risk_cap = alloc.risk_cap

    total_exposure_pct = (total_exposure_q / SIZE_PCT_SCALE) * 100

    return PortfolioAllocation(
        allocations=adjusted_allocations,
        total_exposure_q=total_exposure_q,
        total_exposure_pct=total_exposure_pct,
        correlation_penalties_applied=penalties_applied,
        exposure_cap_applied=exposure_cap_applied,
        portfolio_risk_cap=portfolio_risk_cap,
        rationale=rationale_items if rationale_items else ["standard_allocation"],
    )


def build_correlation_matrix_from_state(
    state_dict: Dict,
    symbols: List[str],
) -> Dict[Tuple[str, str], float]:
    """
    Extract correlation matrix from router state.

    The listener emits rolling_correlation in metrics. Router state may
    cache these for portfolio calculations.

    Args:
        state_dict: Router state dict
        symbols: List of symbols to build matrix for

    Returns:
        Correlation matrix {(sym1, sym2): correlation}
    """
    matrix: Dict[Tuple[str, str], float] = {}

    # Self-correlations = 1.0
    for sym in symbols:
        matrix[(sym, sym)] = 1.0

    # Extract from state if available
    symbols_state = state_dict.get("symbols", {})
    for sym in symbols:
        sym_state = symbols_state.get(sym, {})
        # Check for cached correlation (anchor-relative)
        corr = sym_state.get("rolling_correlation")
        if corr is not None and isinstance(corr, (int, float)):
            # Assume correlation is vs anchor (first symbol by convention)
            anchor = symbols[0] if symbols else None
            if anchor and sym != anchor:
                matrix[(sym, anchor)] = float(corr)
                matrix[(anchor, sym)] = float(corr)

    return matrix
