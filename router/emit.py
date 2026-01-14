"""
Intent and veto emission - Write router events to spine.

Constitutional exhaust port.
Emission boundary: validates before write, fails closed to veto.

v0.3: All intents require expiry fields (valid_until_ts, horizon_minutes, exit_trigger, entry_basis_event_id).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    from router.allocator import AllocationResult
    from router.constraints import VetoReason

from router.envelope import DEFAULT_HORIZON_MINUTES, make_mock_envelope
from router.envelope_provider import Envelope as RealEnvelope
from router.envelope_provider import make_envelope as make_real_envelope
from router.envelope_provider import (
    CompositeEnvelope,
    make_composite_envelope,
    COMPOSITE_KERNEL_VERSION,
    COMPOSITE_KERNEL_BUILD_SHA,
)
from schemas.router_intent import INTENT_SCHEMA_VERSION, validate_router_intent

logger = logging.getLogger(__name__)

# Default exit trigger (v0.3 constitutional)
DEFAULT_EXIT_TRIGGER = "time_stop"

def _parse_source_ts(source_ts: str) -> datetime:
    """Parse source timestamp string to datetime."""
    normalized = source_ts.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)


def _compute_valid_until(source_ts: str, horizon_minutes: int) -> str:
    """Compute valid_until_ts from source_ts and horizon."""
    source_dt = _parse_source_ts(source_ts)
    valid_until_dt = source_dt + timedelta(minutes=horizon_minutes)
    return valid_until_dt.isoformat()


# Canonical float handling (FPDET-1)
try:
    from synthdesk_spine import canonicalize_payload
except ImportError:
    # Fallback if spine SDK not installed (legacy mode)
    def canonicalize_payload(payload: Dict, **kwargs) -> Dict:
        return payload


def _write_event(spine_path: Path, event: Dict) -> bool:
    """
    Write event to spine (internal helper).

    Returns:
        True if written successfully, False on error
    """
    try:
        with spine_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True) + "\n")
            handle.flush()
        return True
    except OSError:
        return False


def _emit_surface_veto(
    spine_path: Path,
    symbol: str,
    validation_error: str,
    source_event_id: str,
    source_ts: str,
) -> None:
    """
    Emit a veto due to surface validation failure.

    This is the fail-closed path: if intent would violate schema,
    we emit typed silence instead. Uses regime_unresolved with
    surface_invalid rationale tag for auditability.
    """
    payload = {
        "symbol": symbol,
        "veto_reason": "regime_unresolved",
        "surface_invalid": validation_error,  # Audit trail
    }
    payload = canonicalize_payload(payload, skip_unknown=True)

    event = {
        "event_type": "router.veto",
        "payload": payload,
        "source_event_id": source_event_id,
        "source_ts": source_ts,
    }

    _write_event(spine_path, event)
    logger.warning(f"SURFACE VETO: intent blocked (validation: {validation_error})")


def emit_intent(
    spine_path: Path,
    symbol: str,
    allocation: "AllocationResult",
    source_event_id: str,
    source_ts: str,
    tick_prices: Optional[List[float]] = None,
    use_real_kernel: bool = True,
    horizon_minutes: int = DEFAULT_HORIZON_MINUTES,
    exit_trigger: str = DEFAULT_EXIT_TRIGGER,
    entry_basis_event_id: Optional[str] = None,
    z_mean: Optional[float] = None,
    z_std: Optional[float] = None,
    regime: Optional[str] = None,
) -> Tuple[bool, Optional[str]]:
    """
    Append router.intent event to spine.

    Uses quantized posture fields from allocator.
    VALIDATES before write - invalid intents become vetoes (fail closed).

    v0.3: Requires expiry fields (valid_until_ts, horizon_minutes, exit_trigger, entry_basis_event_id).
    v0.4: Uses composite envelope (momentum_v0 direction + bootstrap_mc risk) when z_mean available.

    Args:
        spine_path: Path to event_spine.jsonl
        symbol: Symbol identifier
        allocation: AllocationResult with quantized posture fields
        source_event_id: Event ID that triggered this intent
        source_ts: Timestamp from source event
        tick_prices: Recent tick prices for real envelope (optional)
        use_real_kernel: If True and tick_prices provided, use bootstrap MC kernel
        horizon_minutes: Validity horizon in minutes (v0.3 required)
        exit_trigger: Exit condition (v0.3 required)
        entry_basis_event_id: Event that granted authority (defaults to source_event_id)
        z_mean: Normalized drift from regime classifier (for momentum_v0 direction)
        z_std: Normalized volatility (for momentum_v0)
        regime: Current regime label (for momentum_v0)

    Returns:
        (success, error) - success=True if intent emitted, else error explains why
        On validation failure, a veto is emitted instead (fail closed).
    """
    # Compute v0.3 expiry fields
    valid_until_ts = _compute_valid_until(source_ts, horizon_minutes)
    basis_event_id = entry_basis_event_id or source_event_id

    # Build payload with quantized fields and v0.3 expiry
    payload = {
        "symbol": symbol,
        "direction": allocation.direction.value,
        "size_pct_q": allocation.size_pct_q,
        "size_pct_scale": allocation.size_pct_scale,
        "risk_cap": allocation.risk_cap.value,
        "rationale": allocation.rationale,
        # v0.3 required fields
        "schema_version": INTENT_SCHEMA_VERSION,
        "valid_until_ts": valid_until_ts,
        "horizon_minutes": horizon_minutes,
        "exit_trigger": exit_trigger,
        "entry_basis_event_id": basis_event_id,
    }

    # Attach envelope - use composite kernel (momentum_v0 + bootstrap_mc) when z_mean available
    if use_real_kernel and tick_prices and len(tick_prices) >= 100:
        # Use composite envelope: direction from momentum_v0, risk from bootstrap_mc
        composite_env = make_composite_envelope(
            symbol=symbol,
            prices=tick_prices,
            source_event_id=source_event_id,
            z_mean=z_mean,
            z_std=z_std,
            regime=regime,
        )
        payload["envelope"] = composite_env.to_dict()
    else:
        # Fallback to mock envelope with matching horizon
        envelope = make_mock_envelope(
            intent_side=allocation.direction.value,
            confidence=allocation.entropy_factor,
            vetoed=False,
            size=allocation.size_pct_q / allocation.size_pct_scale,
            horizon_minutes=horizon_minutes,
        )
        payload["envelope"] = envelope.to_dict()

    payload = canonicalize_payload(payload, skip_unknown=True)

    # EMISSION BOUNDARY: Validate before write
    try:
        validate_router_intent(payload, source_ts=source_ts)
    except ValueError as e:
        # Fail closed: emit veto instead of invalid intent
        _emit_surface_veto(
            spine_path=spine_path,
            symbol=symbol,
            validation_error=str(e),
            source_event_id=source_event_id,
            source_ts=source_ts,
        )
        return (False, f"surface_invalid: {e}")

    event = {
        "event_type": "router.intent",
        "payload": payload,
        "source_event_id": source_event_id,
        "source_ts": source_ts,
    }

    if _write_event(spine_path, event):
        return (True, None)
    else:
        return (False, "write_failed")


def emit_weak_intent(
    spine_path: Path,
    symbol: str,
    allocation: "AllocationResult",
    source_event_id: str,
    source_ts: str,
    tick_prices: Optional[List[float]] = None,
    use_real_kernel: bool = True,
    horizon_minutes: int = DEFAULT_HORIZON_MINUTES,
    exit_trigger: str = DEFAULT_EXIT_TRIGGER,
    entry_basis_event_id: Optional[str] = None,
    z_mean: Optional[float] = None,
    z_std: Optional[float] = None,
    regime: Optional[str] = None,
) -> Tuple[bool, Optional[str]]:
    """
    Append router.intent_weak event to spine.

    Weak intents are "questions with a directional hypothesis" - they express
    a directional lean but lack conviction for execution authority.

    Properties:
    - NOT gated by authority (questions, not decisions)
    - Do NOT update state.last_allocation (no dedup against them)
    - May update independently of strong intents
    - Scored by EVT-1 but not used for live execution

    v0.3: Requires expiry fields (valid_until_ts, horizon_minutes, exit_trigger, entry_basis_event_id).
    v0.4: Uses composite envelope (momentum_v0 direction + bootstrap_mc risk) when z_mean available.

    Args:
        spine_path: Path to event_spine.jsonl
        symbol: Symbol identifier
        allocation: AllocationResult with quantized posture fields
        source_event_id: Event ID that triggered this intent
        source_ts: Timestamp from source event
        tick_prices: Recent tick prices for real envelope (optional)
        use_real_kernel: If True and tick_prices provided, use bootstrap MC kernel
        horizon_minutes: Validity horizon in minutes (v0.3 required)
        exit_trigger: Exit condition (v0.3 required)
        entry_basis_event_id: Event that granted authority (defaults to source_event_id)
        z_mean: Normalized drift from regime classifier (for momentum_v0 direction)
        z_std: Normalized volatility (for momentum_v0)
        regime: Current regime label (for momentum_v0)

    Returns:
        (success, error) - success=True if intent emitted, else error explains why
        On validation failure, a veto is emitted instead (fail closed).
    """
    # Compute v0.3 expiry fields
    valid_until_ts = _compute_valid_until(source_ts, horizon_minutes)
    basis_event_id = entry_basis_event_id or source_event_id

    # Build payload with quantized fields and v0.3 expiry
    payload = {
        "symbol": symbol,
        "direction": allocation.direction.value,
        "size_pct_q": allocation.size_pct_q,
        "size_pct_scale": allocation.size_pct_scale,
        "risk_cap": allocation.risk_cap.value,
        "rationale": allocation.rationale,
        "strength": "weak",  # Explicit marker
        # v0.3 required fields
        "schema_version": INTENT_SCHEMA_VERSION,
        "valid_until_ts": valid_until_ts,
        "horizon_minutes": horizon_minutes,
        "exit_trigger": exit_trigger,
        "entry_basis_event_id": basis_event_id,
    }

    # Attach envelope - use composite kernel (momentum_v0 + bootstrap_mc) when z_mean available
    if use_real_kernel and tick_prices and len(tick_prices) >= 100:
        # Use composite envelope: direction from momentum_v0, risk from bootstrap_mc
        composite_env = make_composite_envelope(
            symbol=symbol,
            prices=tick_prices,
            source_event_id=source_event_id,
            z_mean=z_mean,
            z_std=z_std,
            regime=regime,
        )
        payload["envelope"] = composite_env.to_dict()
    else:
        # Fallback to mock envelope with matching horizon
        envelope = make_mock_envelope(
            intent_side=allocation.direction.value,
            confidence=allocation.entropy_factor,
            vetoed=False,
            size=allocation.size_pct_q / allocation.size_pct_scale,
            horizon_minutes=horizon_minutes,
        )
        payload["envelope"] = envelope.to_dict()

    payload = canonicalize_payload(payload, skip_unknown=True)

    # EMISSION BOUNDARY: Validate before write
    try:
        validate_router_intent(payload, source_ts=source_ts)
    except ValueError as e:
        # Fail closed: emit veto instead of invalid intent
        _emit_surface_veto(
            spine_path=spine_path,
            symbol=symbol,
            validation_error=str(e),
            source_event_id=source_event_id,
            source_ts=source_ts,
        )
        return (False, f"surface_invalid: {e}")

    event = {
        "event_type": "router.intent_weak",
        "payload": payload,
        "source_event_id": source_event_id,
        "source_ts": source_ts,
    }

    if _write_event(spine_path, event):
        return (True, None)
    else:
        return (False, "write_failed")


def emit_shadow_intent(
    spine_path: Path,
    symbol: str,
    allocation: "AllocationResult",
    blocked_by: str,
    source_event_id: str,
    source_ts: str,
    tick_prices: Optional[List[float]] = None,
    use_real_kernel: bool = True,
    horizon_minutes: int = DEFAULT_HORIZON_MINUTES,
    exit_trigger: str = DEFAULT_EXIT_TRIGGER,
    entry_basis_event_id: Optional[str] = None,
    z_mean: Optional[float] = None,
    z_std: Optional[float] = None,
    regime: Optional[str] = None,
) -> bool:
    """
    Append router.intent_shadow event to spine.

    Shadow intents are counterfactual: what the router WOULD emit if authority
    permitted. They are NOT actionable and carry explicit blocked_by tag.

    Purpose: Feed EVT-1 trials without granting real authority.

    v0.3: Requires expiry fields (valid_until_ts, horizon_minutes, exit_trigger, entry_basis_event_id).
    v0.4: Uses composite envelope (momentum_v0 direction + bootstrap_mc risk) when z_mean available.

    Args:
        spine_path: Path to event_spine.jsonl
        symbol: Symbol identifier
        allocation: AllocationResult (the blocked intent)
        blocked_by: Why this intent was blocked (e.g., "authority_gate")
        source_event_id: Event ID that triggered this intent
        source_ts: Timestamp from source event
        tick_prices: Recent tick prices for real envelope (optional)
        use_real_kernel: If True and tick_prices provided, use bootstrap MC kernel
        horizon_minutes: Validity horizon in minutes (v0.3 required)
        exit_trigger: Exit condition (v0.3 required)
        entry_basis_event_id: Event that granted authority (defaults to source_event_id)
        z_mean: Normalized drift from regime classifier (for momentum_v0 direction)
        z_std: Normalized volatility (for momentum_v0)
        regime: Current regime label (for momentum_v0)

    Returns:
        True if written successfully, False on error
    """
    # Compute v0.3 expiry fields
    valid_until_ts = _compute_valid_until(source_ts, horizon_minutes)
    basis_event_id = entry_basis_event_id or source_event_id

    payload = {
        "symbol": symbol,
        "direction": allocation.direction.value,
        "size_pct_q": allocation.size_pct_q,
        "size_pct_scale": allocation.size_pct_scale,
        "risk_cap": allocation.risk_cap.value,
        "rationale": allocation.rationale,
        "blocked_by": blocked_by,
        "counterfactual": True,
        # v0.3 required fields
        "schema_version": INTENT_SCHEMA_VERSION,
        "valid_until_ts": valid_until_ts,
        "horizon_minutes": horizon_minutes,
        "exit_trigger": exit_trigger,
        "entry_basis_event_id": basis_event_id,
    }

    # Attach envelope - use composite kernel (momentum_v0 + bootstrap_mc) when z_mean available
    if use_real_kernel and tick_prices and len(tick_prices) >= 100:
        # Use composite envelope: direction from momentum_v0, risk from bootstrap_mc
        composite_env = make_composite_envelope(
            symbol=symbol,
            prices=tick_prices,
            source_event_id=source_event_id,
            z_mean=z_mean,
            z_std=z_std,
            regime=regime,
        )
        payload["envelope"] = composite_env.to_dict()
    else:
        # Fallback to mock envelope with matching horizon
        envelope = make_mock_envelope(
            intent_side=allocation.direction.value,
            confidence=allocation.entropy_factor,
            vetoed=False,
            size=allocation.size_pct_q / allocation.size_pct_scale,
            horizon_minutes=horizon_minutes,
        )
        payload["envelope"] = envelope.to_dict()

    payload = canonicalize_payload(payload, skip_unknown=True)

    event = {
        "event_type": "router.intent_shadow",
        "payload": payload,
        "source_event_id": source_event_id,
        "source_ts": source_ts,
    }

    return _write_event(spine_path, event)


def emit_veto(
    spine_path: Path,
    symbol: str,
    veto_reason: "VetoReason",
    source_event_id: str,
    source_ts: str,
) -> bool:
    """
    Append router.veto event to spine.

    Veto = typed silence. No rationale. No narrative.

    Args:
        spine_path: Path to event_spine.jsonl
        symbol: Symbol identifier
        veto_reason: VetoReason enum member
        source_event_id: Event ID that triggered this veto
        source_ts: Timestamp from source event

    Returns:
        True if written successfully, False on error
    """
    payload = {
        "symbol": symbol,
        "veto_reason": veto_reason.value,
    }

    # Attach envelope (vetoed state collapses to zero)
    envelope = make_mock_envelope(
        intent_side="FLAT",
        confidence=0.0,
        vetoed=True,
        size=0.0,
    )
    payload["envelope"] = envelope.to_dict()

    payload = canonicalize_payload(payload, skip_unknown=True)

    event = {
        "event_type": "router.veto",
        "payload": payload,
        "source_event_id": source_event_id,
        "source_ts": source_ts,
    }

    return _write_event(spine_path, event)
