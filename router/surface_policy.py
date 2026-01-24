"""
Veto Surface Policy Decision Engine

DOCTRINE: VETO_TIMESCALE

Pure decision logic that evaluates intended_hold_min against veto surfaces.
Returns ALLOW | BLOCK with reasons and annotations.

Policy Table:
| policy_id           | regime handling                        | micro handling                         |
|---------------------|----------------------------------------|----------------------------------------|
| P_HOLD_BLOCK_REGIME | block if ACTIVE + hold >= turn_on_min  | annotate only                          |
| P_SCALP_BLOCK_MICRO | annotate only                          | block if ACTIVE + hold <= unsafe_up_to |
| P_UNION_SAFETY      | block if either blocks                 | block if either blocks                 |
| P_ANNOTATE_ONLY     | never block                            | never block                            |

Staleness Rules:
- regime STALE/ERROR + policy P_HOLD_BLOCK_REGIME/P_UNION_SAFETY → BLOCK (REGIME_SURFACE_UNRELIABLE)
- micro NO_DATA → never block, annotate MICRO_RISK_UNKNOWN
- micro STALE/ERROR → block only under P_UNION_SAFETY, else annotate

No-Safe-Zone Invariant (FROZEN 2026-01-24):
- When turn_on_min <= unsafe_up_to_min, the feasible horizon band is EMPTY
- Under P_UNION_SAFETY, this blocks ALL holds (both surfaces contribute)
- This is a VALID blocking configuration, NOT an error state
- Semantic: "no hold duration is structurally safe right now"
- Do not "fix" this by adding precedence or fallback logic

Evidence: EVT-0, EVT-1A, EVT-1B (gate2_final_2026-01-20)
Added: 2026-01-23
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from synthdesk_spine.payloads.veto_surface import VetoSurfacePayload


class PolicyId(str, Enum):
    """Available policy modes for surface evaluation."""
    P_HOLD_BLOCK_REGIME = "P_HOLD_BLOCK_REGIME"
    P_SCALP_BLOCK_MICRO = "P_SCALP_BLOCK_MICRO"
    P_UNION_SAFETY = "P_UNION_SAFETY"
    P_ANNOTATE_ONLY = "P_ANNOTATE_ONLY"


class DecisionOutcome(str, Enum):
    """Possible decision outcomes."""
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"


@dataclass(frozen=True)
class BlockReason:
    """
    Describes why a decision was blocked.

    All blocking decisions must cite the specific surface and threshold that triggered the block.
    """
    plane: Optional[str]  # "regime" | "micro" | "system"
    code: str  # E.g., "HOLD_GE_TURN_ON_15", "SCALP_LE_UNSAFE_5"
    policy_id: str
    threshold_min: Optional[int]
    intended_hold_min: Optional[int]
    surface_event_hash: Optional[str]


@dataclass
class SurfaceDecision:
    """
    Complete decision result from policy evaluation.

    Contains everything needed for the router.decision.v1 event.
    """
    asset: str
    intended_hold_min: Optional[int]
    policy_id: str
    policy_defaulted: bool
    outcome: DecisionOutcome
    blocks: List[BlockReason] = field(default_factory=list)
    annotations: List[str] = field(default_factory=list)
    attached_surfaces: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    hold_used: Optional[int] = None
    hold_required: bool = False

    @property
    def allowed(self) -> bool:
        return self.outcome == DecisionOutcome.ALLOW


def _get_surface_hash(surface: Optional[VetoSurfacePayload]) -> Optional[str]:
    """Extract event_hash from surface if available."""
    if surface is None:
        return None
    if surface.integrity is not None:
        return surface.integrity.event_hash
    return None


def _evaluate_regime(
    surface: Optional[VetoSurfacePayload],
    intended_hold_min: int,
    policy_id: PolicyId,
) -> Tuple[Optional[BlockReason], List[str]]:
    """
    Evaluate regime surface for blocking.

    Returns (block_reason, annotations).
    """
    annotations = []

    # No surface provided
    if surface is None:
        annotations.append("REGIME_SURFACE_MISSING")
        # Fail-closed for regime-aware policies
        if policy_id in (PolicyId.P_HOLD_BLOCK_REGIME, PolicyId.P_UNION_SAFETY):
            return (
                BlockReason(
                    plane="regime",
                    code="REGIME_SURFACE_MISSING",
                    policy_id=policy_id.value,
                    threshold_min=None,
                    intended_hold_min=intended_hold_min,
                    surface_event_hash=None,
                ),
                annotations,
            )
        return (None, annotations)

    # Surface in error or stale state
    if surface.status in ("STALE", "ERROR"):
        annotations.append(f"REGIME_SURFACE_{surface.status}")
        # Fail-closed for regime-aware policies
        if policy_id in (PolicyId.P_HOLD_BLOCK_REGIME, PolicyId.P_UNION_SAFETY):
            return (
                BlockReason(
                    plane="regime",
                    code=f"REGIME_SURFACE_{surface.status}",
                    policy_id=policy_id.value,
                    threshold_min=None,
                    intended_hold_min=intended_hold_min,
                    surface_event_hash=_get_surface_hash(surface),
                ),
                annotations,
            )
        return (None, annotations)

    # NO_DATA regime surface - fail-closed for regime-aware policies
    if surface.status == "NO_DATA":
        annotations.append("REGIME_SURFACE_NO_DATA")
        if policy_id in (PolicyId.P_HOLD_BLOCK_REGIME, PolicyId.P_UNION_SAFETY):
            return (
                BlockReason(
                    plane="regime",
                    code="REGIME_SURFACE_NO_DATA",
                    policy_id=policy_id.value,
                    threshold_min=None,
                    intended_hold_min=intended_hold_min,
                    surface_event_hash=_get_surface_hash(surface),
                ),
                annotations,
            )
        return (None, annotations)

    # ACTIVE surface - check threshold
    if surface.status == "ACTIVE":
        turn_on_min = surface.turn_on_min

        # If annotate-only policy, never block
        if policy_id == PolicyId.P_ANNOTATE_ONLY:
            if turn_on_min is not None and intended_hold_min >= turn_on_min:
                annotations.append(f"REGIME_WOULD_BLOCK_AT_{turn_on_min}M")
            return (None, annotations)

        # If scalp policy, annotate but don't block regime
        if policy_id == PolicyId.P_SCALP_BLOCK_MICRO:
            if turn_on_min is not None and intended_hold_min >= turn_on_min:
                annotations.append(f"REGIME_WOULD_BLOCK_AT_{turn_on_min}M")
            return (None, annotations)

        # For P_HOLD_BLOCK_REGIME and P_UNION_SAFETY: block if hold >= turn_on_min
        if turn_on_min is not None and intended_hold_min >= turn_on_min:
            return (
                BlockReason(
                    plane="regime",
                    code=f"HOLD_GE_TURN_ON_{turn_on_min}",
                    policy_id=policy_id.value,
                    threshold_min=turn_on_min,
                    intended_hold_min=intended_hold_min,
                    surface_event_hash=_get_surface_hash(surface),
                ),
                annotations,
            )

    return (None, annotations)


def _evaluate_micro(
    surface: Optional[VetoSurfacePayload],
    intended_hold_min: int,
    policy_id: PolicyId,
) -> Tuple[Optional[BlockReason], List[str]]:
    """
    Evaluate micro surface for blocking.

    Returns (block_reason, annotations).

    NOTE: Micro semantics are INVERTED from regime.
    Micro blocks SHORT holds (scalps), regime blocks LONG holds.
    """
    annotations = []

    # No surface provided
    if surface is None:
        annotations.append("MICRO_SURFACE_MISSING")
        # Only fail-closed for union safety
        if policy_id == PolicyId.P_UNION_SAFETY:
            return (
                BlockReason(
                    plane="micro",
                    code="MICRO_SURFACE_MISSING",
                    policy_id=policy_id.value,
                    threshold_min=None,
                    intended_hold_min=intended_hold_min,
                    surface_event_hash=None,
                ),
                annotations,
            )
        return (None, annotations)

    # NO_DATA micro surface - NEVER block, always annotate
    # (no orderbook evidence = unknown, not forbidden)
    if surface.status == "NO_DATA":
        annotations.append("MICRO_RISK_UNKNOWN")
        return (None, annotations)

    # Surface in error or stale state
    if surface.status in ("STALE", "ERROR"):
        annotations.append(f"MICRO_SURFACE_{surface.status}")
        # Only fail-closed for union safety
        if policy_id == PolicyId.P_UNION_SAFETY:
            return (
                BlockReason(
                    plane="micro",
                    code=f"MICRO_SURFACE_{surface.status}",
                    policy_id=policy_id.value,
                    threshold_min=None,
                    intended_hold_min=intended_hold_min,
                    surface_event_hash=_get_surface_hash(surface),
                ),
                annotations,
            )
        return (None, annotations)

    # ACTIVE surface - check threshold
    if surface.status == "ACTIVE":
        unsafe_up_to_min = surface.unsafe_up_to_min

        # If annotate-only policy, never block
        if policy_id == PolicyId.P_ANNOTATE_ONLY:
            if unsafe_up_to_min is not None and intended_hold_min <= unsafe_up_to_min:
                annotations.append(f"MICRO_WOULD_BLOCK_LE_{unsafe_up_to_min}M")
            return (None, annotations)

        # If hold policy, annotate but don't block micro
        if policy_id == PolicyId.P_HOLD_BLOCK_REGIME:
            if unsafe_up_to_min is not None and intended_hold_min <= unsafe_up_to_min:
                annotations.append(f"MICRO_WOULD_BLOCK_LE_{unsafe_up_to_min}M")
            return (None, annotations)

        # For P_SCALP_BLOCK_MICRO and P_UNION_SAFETY: block if hold <= unsafe_up_to_min
        # NOTE: This is the INVERTED semantic - blocking SHORT holds
        if unsafe_up_to_min is not None and intended_hold_min <= unsafe_up_to_min:
            return (
                BlockReason(
                    plane="micro",
                    code=f"SCALP_LE_UNSAFE_{unsafe_up_to_min}",
                    policy_id=policy_id.value,
                    threshold_min=unsafe_up_to_min,
                    intended_hold_min=intended_hold_min,
                    surface_event_hash=_get_surface_hash(surface),
                ),
                annotations,
            )

    return (None, annotations)


def evaluate_surfaces(
    asset: str,
    intended_hold_min: int,
    regime_surface: Optional[VetoSurfacePayload],
    micro_surface: Optional[VetoSurfacePayload],
    policy_id: PolicyId = PolicyId.P_HOLD_BLOCK_REGIME,
    policy_defaulted: bool = False,
) -> SurfaceDecision:
    """
    Evaluate veto surfaces and return a decision.

    SEMANTICS (CRITICAL):
    - Regime blocks LONG holds: intended_hold_min >= turn_on_min
    - Micro blocks SHORT holds: intended_hold_min <= unsafe_up_to_min
    - These are INVERTED by design.

    Args:
        asset: Trading pair symbol
        intended_hold_min: REQUIRED - proposed holding period in minutes
        regime_surface: Regime plane veto surface (or None)
        micro_surface: Micro plane veto surface (or None)
        policy_id: Which policy to apply (default: P_HOLD_BLOCK_REGIME)
        policy_defaulted: True if policy_id was not explicitly specified

    Returns:
        SurfaceDecision with outcome, blocks, and annotations

    Raises:
        ValueError: If intended_hold_min is not a positive integer
    """
    # Validate intended_hold_min (HARD REQUIRED, never default)
    if not isinstance(intended_hold_min, int) or intended_hold_min <= 0:
        raise ValueError(
            f"intended_hold_min must be a positive integer, got: {intended_hold_min}"
        )

    blocks = []
    annotations = []

    # Evaluate regime
    regime_block, regime_annotations = _evaluate_regime(
        regime_surface, intended_hold_min, policy_id
    )
    annotations.extend(regime_annotations)
    if regime_block is not None:
        blocks.append(regime_block)

    # Evaluate micro
    micro_block, micro_annotations = _evaluate_micro(
        micro_surface, intended_hold_min, policy_id
    )
    annotations.extend(micro_annotations)
    if micro_block is not None:
        blocks.append(micro_block)

    # Determine outcome
    outcome = DecisionOutcome.BLOCK if blocks else DecisionOutcome.ALLOW

    attached_surfaces = build_attached_surfaces(regime_surface, micro_surface)

    return SurfaceDecision(
        asset=asset,
        intended_hold_min=intended_hold_min,
        policy_id=policy_id.value,
        policy_defaulted=policy_defaulted,
        outcome=outcome,
        blocks=blocks,
        annotations=annotations,
        attached_surfaces=attached_surfaces,
        hold_used=intended_hold_min,
        hold_required=False,
    )


def build_attached_surfaces(
    regime_surface: Optional[VetoSurfacePayload],
    micro_surface: Optional[VetoSurfacePayload],
) -> Dict[str, Dict[str, Any]]:
    """Build the surface presence dict for router decision payloads."""
    surfaces: Dict[str, Dict[str, Any]] = {}

    if regime_surface is not None:
        surfaces["regime"] = {
            "present": True,
            "event_hash": _get_surface_hash(regime_surface),
            "status": regime_surface.status,
            "turn_on_min": regime_surface.turn_on_min,
        }
    else:
        surfaces["regime"] = {"present": False}

    if micro_surface is not None:
        surfaces["micro"] = {
            "present": True,
            "event_hash": _get_surface_hash(micro_surface),
            "status": micro_surface.status,
            "unsafe_up_to_min": micro_surface.unsafe_up_to_min,
        }
    else:
        surfaces["micro"] = {"present": False}

    return surfaces


def decision_to_dict(decision: SurfaceDecision) -> Dict[str, Any]:
    """Convert SurfaceDecision to JSON-serializable dict for events."""
    return {
        "asset": decision.asset,
        "intended_hold_min": decision.intended_hold_min,
        "policy_id": decision.policy_id,
        "policy_defaulted": decision.policy_defaulted,
        "allowed": decision.allowed,
        "outcome": decision.outcome.value,
        "hold_used": decision.hold_used,
        "hold_required": decision.hold_required,
        "blocks": [
            {
                "plane": b.plane,
                "code": b.code,
                "policy_id": b.policy_id,
                "threshold_min": b.threshold_min,
                "intended_hold_min": b.intended_hold_min,
                "surface_event_hash": b.surface_event_hash,
            }
            for b in decision.blocks
        ],
        "annotations": decision.annotations,
        "attached_surfaces": decision.attached_surfaces,
    }
