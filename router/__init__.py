"""Router v0.2 - Deterministic intent synthesis runtime with authority tiers."""

__version__ = "0.2.0"

from .authority import (
    AuthorityLevel,
    AuthorityState,
    DemotionEvent,
    DemotionWatcher,
    bind_authority,
    create_build_meta_check,
    create_violation_active_check,
)
from .allocator import (
    Regime,
    RiskCap,
    Direction,
    EntropyState,
    AllocationResult,
    allocate,
    infer_regime,
    compute_allocation_from_state,
    SIZE_PCT_SCALE,
)
from .state import RouterState

__all__ = [
    # Authority
    "AuthorityLevel",
    "AuthorityState",
    "DemotionEvent",
    "DemotionWatcher",
    "bind_authority",
    "create_build_meta_check",
    "create_violation_active_check",
    # Allocator
    "Regime",
    "RiskCap",
    "Direction",
    "EntropyState",
    "AllocationResult",
    "allocate",
    "infer_regime",
    "compute_allocation_from_state",
    "SIZE_PCT_SCALE",
    # State
    "RouterState",
]
