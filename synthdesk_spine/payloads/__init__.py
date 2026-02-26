"""
Typed payload dataclasses.

Semantic contracts for event-specific data.

All payloads:
- Are immutable (frozen=True)
- Have no optional fields (unless unavoidable)
- Contain no behavior
- Have no defaults
"""

from .market import MarketRegimePayload, MarketRegimeChangePayload
from .router import RouterIntentPayload, RouterPermissionPayload
from .listener import (
    ListenerStartPayload,
    ListenerStopPayload,
    ListenerCrashPayload,
)
from .veto_surface import (
    HorizonRisk,
    VetoSurfaceInputs,
    VetoSurfaceIntegrity,
    VetoSurfacePayload,
    make_regime_surface,
    make_micro_surface,
    make_micro_stub,
)

__all__ = [
    "MarketRegimePayload",
    "MarketRegimeChangePayload",
    "RouterIntentPayload",
    "RouterPermissionPayload",
    "ListenerStartPayload",
    "ListenerStopPayload",
    "ListenerCrashPayload",
    # Veto Surface (v1)
    "HorizonRisk",
    "VetoSurfaceInputs",
    "VetoSurfaceIntegrity",
    "VetoSurfacePayload",
    "make_regime_surface",
    "make_micro_surface",
    "make_micro_stub",
]
