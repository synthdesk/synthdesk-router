"""
Veto Surface Payload Schema (v1)

This schema represents horizon-indexed abstention dominance as a surface.
Two planes exist: regime (long-horizon risk) and micro (short-horizon risk).

CRITICAL SEMANTIC DISTINCTION:
- REGIME plane uses `turn_on_min`: abstention dominates for holds >= turn_on_min
- MICRO plane uses `unsafe_up_to_min`: scalping unsafe for holds <= unsafe_up_to_min

These semantics are INVERTED. Never use turn_on_min for micro or unsafe_up_to_min for regime.

Invariant: Only one of (turn_on_min, unsafe_up_to_min) may be non-null per surface,
and it must match the plane type.

Evidence: EVT-0, EVT-1A, EVT-1B (gate2_final_2026-01-20)
Doctrine: docs/DOCTRINE_VETO_TIMESCALE.md
Added: 2026-01-23
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class HorizonRisk:
    """
    Risk metrics for a single horizon within a veto surface.

    All metrics in basis points (bps) for consistency.
    """
    status: str  # "DOMINANCE" | "NO_DOMINANCE" | "UNKNOWN"
    net_bps: Optional[float] = None  # ELV - FAC (positive = abstention wins)
    cvar10_bps: Optional[float] = None  # CVaR at 10% (more negative = worse tail)
    elv_bps: Optional[float] = None  # Expected Loss during Veto
    conf: Optional[float] = None  # Confidence [0, 1]


@dataclass(frozen=True)
class VetoSurfaceInputs:
    """Provenance tracking for court traceability."""
    evidence_refs: Tuple[str, ...]  # Paths/IDs of admitted artifacts used
    input_hash: str  # SHA256 of input data
    params_hash: str  # SHA256 of parameters
    code_sha: str  # Git SHA of emitting code


@dataclass(frozen=True)
class VetoSurfaceIntegrity:
    """Chain of custody for event integrity."""
    event_hash: str  # SHA256 of this event (excluding this field)
    prev_event_hash: Optional[str] = None  # Link to previous surface event


@dataclass(frozen=True)
class VetoSurfacePayload:
    """
    Veto Surface Payload (v1).

    Represents horizon-indexed abstention dominance for a single asset and plane.

    SEMANTIC RULES (FROZEN):

    1. REGIME plane (long-horizon risk):
       - Uses `turn_on_min`: earliest horizon where abstention dominates
       - Gate: block if intended_hold_min >= turn_on_min
       - `unsafe_up_to_min` MUST be None

    2. MICRO plane (short-horizon risk):
       - Uses `unsafe_up_to_min`: scalping unsafe up to this horizon
       - Gate: block if intended_hold_min <= unsafe_up_to_min
       - `turn_on_min` MUST be None

    These semantics are INVERTED by design. Do not mix.

    Args:
        asset: Trading pair symbol (e.g., "BTCUSDT")
        plane: Surface plane ("regime" | "micro")
        schema_version: Always "1.0" for v1
        status: "ACTIVE" | "NO_DATA" | "STALE" | "ERROR"
        horizons_min: Tuple of horizons evaluated (e.g., (5, 15, 30, 60, 240))
        turn_on_min: REGIME ONLY - earliest horizon where abstention dominates
        unsafe_up_to_min: MICRO ONLY - scalping unsafe up to this horizon
        risk_by_horizon: Per-horizon risk metrics (key is str(horizon))
        reasons: Explanation dict with "regime" and "micro" keys (both required)
        inputs: Provenance tracking
        integrity: Chain of custody
    """
    asset: str
    plane: str  # "regime" | "micro"
    schema_version: str  # "1.0"
    status: str  # "ACTIVE" | "NO_DATA" | "STALE" | "ERROR"
    horizons_min: Tuple[int, ...]
    turn_on_min: Optional[int]  # REGIME ONLY
    unsafe_up_to_min: Optional[int]  # MICRO ONLY
    risk_by_horizon: Dict[str, HorizonRisk]  # "15" -> HorizonRisk
    reasons: Dict[str, List[str]]  # {"regime": [...], "micro": [...]}
    inputs: VetoSurfaceInputs
    integrity: Optional[VetoSurfaceIntegrity] = None

    def __post_init__(self):
        """Validate plane-specific semantic constraints."""
        # Validate plane value
        if self.plane not in ("regime", "micro"):
            raise ValueError(f"Invalid plane: {self.plane}. Must be 'regime' or 'micro'.")

        # Validate schema version
        if self.schema_version != "1.0":
            raise ValueError(f"Invalid schema_version: {self.schema_version}. Must be '1.0'.")

        # Validate status
        valid_statuses = ("ACTIVE", "NO_DATA", "STALE", "ERROR")
        if self.status not in valid_statuses:
            raise ValueError(f"Invalid status: {self.status}. Must be one of {valid_statuses}.")

        # CRITICAL: Enforce plane-specific horizon threshold semantics
        if self.plane == "regime":
            if self.unsafe_up_to_min is not None:
                raise ValueError(
                    "REGIME plane must not use unsafe_up_to_min. "
                    "Regime semantics use turn_on_min (block if hold >= turn_on_min)."
                )
        elif self.plane == "micro":
            if self.turn_on_min is not None:
                raise ValueError(
                    "MICRO plane must not use turn_on_min. "
                    "Micro semantics use unsafe_up_to_min (block if hold <= unsafe_up_to_min)."
                )

        # Validate reasons contains both keys
        if "regime" not in self.reasons or "micro" not in self.reasons:
            raise ValueError(
                "reasons must contain both 'regime' and 'micro' keys (even if empty lists)."
            )

        # Validate horizons_min is non-empty for ACTIVE status
        if self.status == "ACTIVE" and len(self.horizons_min) == 0:
            raise ValueError("horizons_min must not be empty when status is ACTIVE.")

        # Validate risk_by_horizon keys are subset of horizons_min
        valid_horizon_keys = {str(h) for h in self.horizons_min}
        actual_keys = set(self.risk_by_horizon.keys())
        invalid_keys = actual_keys - valid_horizon_keys
        if invalid_keys:
            raise ValueError(
                f"risk_by_horizon contains keys {invalid_keys} not in horizons_min {self.horizons_min}."
            )

        # Validate NO_DATA surfaces have empty risk_by_horizon
        # (NO_DATA may declare horizons to show intended grid, but must not claim risk metrics)
        if self.status == "NO_DATA" and len(self.risk_by_horizon) > 0:
            raise ValueError(
                "NO_DATA surfaces must have empty risk_by_horizon. "
                "horizons_min may be populated to declare intended grid."
            )

    def get_threshold(self) -> Optional[int]:
        """
        Get the active threshold for this surface's plane.

        Returns:
            turn_on_min for regime, unsafe_up_to_min for micro, or None if not set.
        """
        if self.plane == "regime":
            return self.turn_on_min
        else:
            return self.unsafe_up_to_min

    def should_block(self, intended_hold_min: int) -> bool:
        """
        Check if this surface would block the given hold duration.

        SEMANTIC NOTE:
        - Regime blocks LONGER holds: intended_hold_min >= turn_on_min
        - Micro blocks SHORTER holds: intended_hold_min <= unsafe_up_to_min

        Args:
            intended_hold_min: Proposed holding period in minutes

        Returns:
            True if this surface would block, False otherwise.
            Always False if status is not ACTIVE or threshold is None.
        """
        if self.status != "ACTIVE":
            return False

        threshold = self.get_threshold()
        if threshold is None:
            return False

        if self.plane == "regime":
            # Regime: block long holds
            return intended_hold_min >= threshold
        else:
            # Micro: block short holds (scalps)
            return intended_hold_min <= threshold


# Factory functions for creating valid surfaces

def make_regime_surface(
    asset: str,
    horizons_min: Tuple[int, ...],
    turn_on_min: Optional[int],
    risk_by_horizon: Dict[str, HorizonRisk],
    reasons_regime: List[str],
    inputs: VetoSurfaceInputs,
    status: str = "ACTIVE",
    integrity: Optional[VetoSurfaceIntegrity] = None,
) -> VetoSurfacePayload:
    """Factory for creating a regime surface with correct semantics."""
    return VetoSurfacePayload(
        asset=asset,
        plane="regime",
        schema_version="1.0",
        status=status,
        horizons_min=horizons_min,
        turn_on_min=turn_on_min,
        unsafe_up_to_min=None,  # MUST be None for regime
        risk_by_horizon=risk_by_horizon,
        reasons={"regime": reasons_regime, "micro": []},
        inputs=inputs,
        integrity=integrity,
    )


def make_micro_surface(
    asset: str,
    horizons_min: Tuple[int, ...],
    unsafe_up_to_min: Optional[int],
    risk_by_horizon: Dict[str, HorizonRisk],
    reasons_micro: List[str],
    inputs: VetoSurfaceInputs,
    status: str = "ACTIVE",
    integrity: Optional[VetoSurfaceIntegrity] = None,
) -> VetoSurfacePayload:
    """Factory for creating a micro surface with correct semantics."""
    return VetoSurfacePayload(
        asset=asset,
        plane="micro",
        schema_version="1.0",
        status=status,
        horizons_min=horizons_min,
        turn_on_min=None,  # MUST be None for micro
        unsafe_up_to_min=unsafe_up_to_min,
        risk_by_horizon=risk_by_horizon,
        reasons={"regime": [], "micro": reasons_micro},
        inputs=inputs,
        integrity=integrity,
    )


def make_micro_stub(
    asset: str,
    inputs: VetoSurfaceInputs,
    integrity: Optional[VetoSurfaceIntegrity] = None,
) -> VetoSurfacePayload:
    """
    Factory for creating a micro stub surface (NO_DATA).

    This is the canonical shape for micro surfaces before orderbook admission.
    It explicitly includes both threshold fields as None to show structural
    absence, not "safe to scalp".
    """
    return VetoSurfacePayload(
        asset=asset,
        plane="micro",
        schema_version="1.0",
        status="NO_DATA",
        horizons_min=(3, 5, 10),  # Micro horizon grid
        turn_on_min=None,  # Explicit None (not used for micro)
        unsafe_up_to_min=None,  # Explicit None (no data, not "safe")
        risk_by_horizon={},  # Empty - no data
        reasons={"regime": [], "micro": ["NO_ORDERBOOK_ADMISSION"]},
        inputs=inputs,
        integrity=integrity,
    )
