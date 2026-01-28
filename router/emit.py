"""
Intent and veto emission - Write router events to spine.

Constitutional exhaust port.
Emission boundary: validates before write, fails closed to veto.

v0.3: All intents require expiry fields (valid_until_ts, horizon_minutes, exit_trigger, entry_basis_event_id).
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    from router.allocator import AllocationResult
    from router.constraints import VetoReason
    from emission.speech_counters import SpeechCycle

from router.envelope import DEFAULT_HORIZON_MINUTES, make_mock_envelope
from emission.speech_v1 import emit_speech_v1
from router.envelope_provider import Envelope as RealEnvelope
from router.envelope_provider import make_envelope as make_real_envelope
from router.envelope_provider import (
    CompositeEnvelope,
    make_composite_envelope,
    COMPOSITE_KERNEL_VERSION,
    COMPOSITE_KERNEL_BUILD_SHA,
)
from schemas.router_intent import INTENT_SCHEMA_VERSION, validate_router_intent
from synthdesk_spine.event_types import (
    ROUTER_DECISION_V1,
    ROUTER_VETO,
    ROUTER_INTENT,
    ROUTER_INTENT_WEAK,
    ROUTER_INTENT_SHADOW,
    ROUTER_HEARTBEAT,
    ROUTER_HEALTH_SUMMARY,
)

logger = logging.getLogger(__name__)

# Module-level build identity (set by main.py at startup)
_ROUTER_BUILD_SHA: Optional[str] = None
_SOAK_CONTRACT_HASH: Optional[str] = None


def set_router_build_sha(sha: str) -> None:
    """Set router build SHA for all emissions. Called once at startup."""
    global _ROUTER_BUILD_SHA
    _ROUTER_BUILD_SHA = sha


def get_router_build_sha() -> Optional[str]:
    """Get current router build SHA."""
    return _ROUTER_BUILD_SHA


def set_soak_contract_hash(hash: str) -> None:
    """Set soak contract hash for heartbeat emissions."""
    global _SOAK_CONTRACT_HASH
    _SOAK_CONTRACT_HASH = hash


def get_soak_contract_hash() -> Optional[str]:
    """Get current soak contract hash."""
    return _SOAK_CONTRACT_HASH

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


def _write_event(
    spine_path: Path,
    event: Dict,
    speech_cycle: Optional["SpeechCycle"] = None,
) -> bool:
    """
    Write event to spine (internal helper).

    Automatically attaches router_build_sha if set.

    Returns:
        True if written successfully, False on error
    """
    if event.get("event_type") != "router.speech.v1":
        speech_event, silence_reason = emit_speech_v1(event)
        if speech_cycle is not None:
            speech_cycle.observe(speech_event, silence_reason)
        if speech_event is not None:
            _write_event(spine_path, speech_event, None)

    # Attach build identity to every event (Gate 1 requirement)
    if _ROUTER_BUILD_SHA is not None:
        event["router_build_sha"] = _ROUTER_BUILD_SHA

    try:
        with spine_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True) + "\n")
            handle.flush()
        return True
    except OSError:
        return False


def _compute_intent_id(payload: Dict, source_event_id: str) -> str:
    """Compute deterministic intent_id from payload + source_event_id."""
    payload_str = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256((source_event_id + payload_str).encode("utf-8")).hexdigest()
    return digest


def _emit_surface_veto(
    spine_path: Path,
    symbol: str,
    validation_error: str,
    source_event_id: str,
    source_ts: str,
    speech_cycle: Optional["SpeechCycle"] = None,
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
        "event_type": ROUTER_VETO,
        "payload": payload,
        "source_event_id": source_event_id,
        "source_ts": source_ts,
    }

    _write_event(spine_path, event, speech_cycle)
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
    regime_confidence: Optional[float] = None,
    speech_cycle: Optional["SpeechCycle"] = None,
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
            regime_confidence=regime_confidence,
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

    # Binding provenance (payload-level, deterministic)
    payload["source_event_id"] = source_event_id
    payload["source_ts"] = source_ts

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
            speech_cycle=speech_cycle,
        )
        return (False, f"surface_invalid: {e}")

    payload["intent_id"] = _compute_intent_id(payload, source_event_id)

    event = {
        "event_type": ROUTER_INTENT,
        "payload": payload,
        "source_event_id": source_event_id,
        "source_ts": source_ts,
    }

    if _write_event(spine_path, event, speech_cycle):
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
    regime_confidence: Optional[float] = None,
    speech_cycle: Optional["SpeechCycle"] = None,
) -> Tuple[bool, Optional[str]]:
    """
    Append router.intent_weak event to spine.

    Weak intents are "questions with a directional hypothesis" - they express
    a directional lean but posterior mass is insufficient for execution authority.

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
            regime_confidence=regime_confidence,
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

    # Binding provenance (payload-level, deterministic)
    payload["source_event_id"] = source_event_id
    payload["source_ts"] = source_ts

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
            speech_cycle=speech_cycle,
        )
        return (False, f"surface_invalid: {e}")

    payload["intent_id"] = _compute_intent_id(payload, source_event_id)

    event = {
        "event_type": ROUTER_INTENT_WEAK,
        "payload": payload,
        "source_event_id": source_event_id,
        "source_ts": source_ts,
    }

    if _write_event(spine_path, event, speech_cycle):
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
    regime_confidence: Optional[float] = None,
    speech_cycle: Optional["SpeechCycle"] = None,
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
            regime_confidence=regime_confidence,
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

    # Binding provenance (payload-level, deterministic)
    payload["source_event_id"] = source_event_id
    payload["source_ts"] = source_ts

    payload = canonicalize_payload(payload, skip_unknown=True)

    payload["intent_id"] = _compute_intent_id(payload, source_event_id)

    event = {
        "event_type": ROUTER_INTENT_SHADOW,
        "payload": payload,
        "source_event_id": source_event_id,
        "source_ts": source_ts,
    }

    return _write_event(spine_path, event, speech_cycle)


def emit_veto(
    spine_path: Path,
    symbol: str,
    veto_reason: "VetoReason",
    source_event_id: str,
    source_ts: str,
    speech_cycle: Optional["SpeechCycle"] = None,
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
        "event_type": ROUTER_VETO,
        "payload": payload,
        "source_event_id": source_event_id,
        "source_ts": source_ts,
    }

    return _write_event(spine_path, event, speech_cycle)


def emit_heartbeat(
    spine_path: Path,
    authority_level: str,
    spine_offset: int,
    events_consumed: int,
    intents_emitted: int,
    vetoes_emitted: int,
    shadows_emitted: int,
    last_event_ts: Optional[str] = None,
) -> bool:
    """
    Append router.heartbeat event to spine.

    Heartbeat proves router liveness. Emitted on time interval regardless of
    event activity. Critical for detecting silent failures.

    Args:
        spine_path: Path to event_spine.jsonl
        authority_level: Current authority level (e.g., "v0.1", "v0.2")
        spine_offset: Current byte offset in spine
        events_consumed: Total events consumed this session
        intents_emitted: Total intents emitted this session
        vetoes_emitted: Total vetoes emitted this session
        shadows_emitted: Total shadow intents emitted this session
        last_event_ts: Timestamp of last consumed event (None if no events yet)

    Returns:
        True if written successfully, False on error
    """
    now = datetime.now(timezone.utc)

    payload = {
        "authority_level": authority_level,
        "spine_offset": spine_offset,
        "events_consumed": events_consumed,
        "intents_emitted": intents_emitted,
        "vetoes_emitted": vetoes_emitted,
        "shadows_emitted": shadows_emitted,
        "last_event_ts": last_event_ts,
    }

    # Include soak contract hash if set
    if _SOAK_CONTRACT_HASH is not None:
        payload["soak_contract_hash"] = _SOAK_CONTRACT_HASH

    event = {
        "event_type": ROUTER_HEARTBEAT,
        "timestamp": now.isoformat(),
        "payload": payload,
    }

    return _write_event(spine_path, event)


def emit_health_summary(
    spine_path: Path,
    authority_level: str,
    window_s: int,
    events_in_window: int,
    intents_in_window: int,
    vetoes_in_window: int,
    shadows_in_window: int,
    violations_in_window: int,
    per_symbol_events: Dict[str, int],
    per_symbol_regimes: Dict[str, str],
    lag_s: float,
    last_event_ts: Optional[str],
    uptime_s: float,
) -> bool:
    """
    Append router.health_summary event to spine.

    Summarizes recent router activity for dashboards and alerting.
    """
    now = datetime.now(timezone.utc)

    payload = {
        "authority_level": authority_level,
        "window_s": window_s,
        "events_in": events_in_window,
        "intents": intents_in_window,
        "vetoes": vetoes_in_window,
        "shadows": shadows_in_window,
        "violations": violations_in_window,
        "per_symbol_events": per_symbol_events,
        "per_symbol_regimes": per_symbol_regimes,
        "lag_s": round(lag_s, 2),
        "last_event_ts": last_event_ts,
        "uptime_s": round(uptime_s, 2),
    }

    if window_s > 0:
        payload["event_rate_per_min"] = round(events_in_window / (window_s / 60.0), 2)
    if vetoes_in_window > 0:
        payload["intent_veto_ratio"] = round(intents_in_window / vetoes_in_window, 2)

    event = {
        "event_type": ROUTER_HEALTH_SUMMARY,
        "timestamp": now.isoformat(),
        "payload": payload,
    }

    return _write_event(spine_path, event)


def emit_decision(
    spine_path: Path,
    symbol: str,
    intended_hold_min: int,
    policy_id: str,
    policy_defaulted: bool,
    allowed: bool,
    veto_reason: Optional["VetoReason"],
    blocks: List[Dict],
    annotations: List[str],
    attached_surfaces: Dict[str, Dict],
    source_event_id: str,
    source_ts: str,
    decision_authority: Optional[str] = None,
    conflict_class: Optional[str] = None,
    dominant_plane: Optional[str] = None,
    conflict_score: Optional[float] = None,
) -> bool:
    """
    Append router.decision.v1 event to spine.

    DOCTRINE: VETO_TIMESCALE

    Decision events capture the full surface gate evaluation result,
    providing audit trail and replay capability for time-scale aware blocking.

    This is an EXPLANATORY event, not an ACTION event. It explains why
    an intent was emitted or why a veto occurred based on surface evaluation.

    Args:
        spine_path: Path to event_spine.jsonl
        symbol: Symbol identifier
        intended_hold_min: The holding period that was evaluated (REQUIRED)
        policy_id: Policy used for evaluation (e.g., "P_HOLD_BLOCK_REGIME")
        policy_defaulted: True if policy_id was defaulted (not explicit)
        allowed: True if surfaces allowed the action
        veto_reason: VetoReason if blocked by surface, None if allowed
        blocks: List of block reasons from surface evaluation
        annotations: List of annotations from surface evaluation
        attached_surfaces: Dict of surface references (regime, micro)
        source_event_id: Event ID that triggered this decision
        source_ts: Timestamp from source event
        decision_authority: Authority level at decision time ("shadow", "weak", "real")
                          Defaults to "real" for backward compatibility.
        conflict_class: Classification of surface plane agreement/disagreement (1.2+)
        dominant_plane: Plane that determined outcome, if applicable (1.2+)
        conflict_score: Conflict intensity 0.0-1.0 (1.2+)

    Returns:
        True if written successfully, False on error
    """
    # Auto-compute classification if not provided (Amendment 1.2)
    if conflict_class is None and conflict_score is None and dominant_plane is None:
        from schemas.router_decision import compute_conflict_classification

        regime_present = attached_surfaces.get("regime", {}).get("present", False)
        micro_present = attached_surfaces.get("micro", {}).get("present", False)
        regime_would_block = any(b.get("plane") == "regime" for b in blocks)
        micro_would_block = any(b.get("plane") == "micro" for b in blocks)

        conflict_class, dominant_plane, conflict_score = compute_conflict_classification(
            regime_present=regime_present,
            regime_would_block=regime_would_block,
            micro_present=micro_present,
            micro_would_block=micro_would_block,
        )

    payload = {
        "symbol": symbol,
        "intended_hold_min": intended_hold_min,
        "policy_id": policy_id,
        "policy_defaulted": policy_defaulted,
        "allowed": allowed,
        "veto_reason": veto_reason.value if veto_reason else None,
        "blocks": blocks,
        "annotations": annotations,
        "attached_surfaces": attached_surfaces,
        "schema_version": "1.2",
        "decision_authority": decision_authority or "real",
        "conflict_class": conflict_class,
        "dominant_plane": dominant_plane,
        "conflict_score": conflict_score,
    }

    payload = canonicalize_payload(payload, skip_unknown=True)

    event = {
        "event_type": ROUTER_DECISION_V1,
        "payload": payload,
        "source_event_id": source_event_id,
        "source_ts": source_ts,
    }

    return _write_event(spine_path, event)
