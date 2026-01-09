"""
Intent and veto emission - Write router events to spine.

Constitutional exhaust port.
Emission boundary: validates before write, fails closed to veto.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Dict, Optional, Tuple

if TYPE_CHECKING:
    from router.allocator import AllocationResult
    from router.constraints import VetoReason

from router.envelope import make_mock_envelope
from schemas.router_intent import validate_router_intent

logger = logging.getLogger(__name__)

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
) -> Tuple[bool, Optional[str]]:
    """
    Append router.intent event to spine.

    Uses quantized posture fields from allocator (v0.2 schema).
    VALIDATES before write - invalid intents become vetoes (fail closed).

    Args:
        spine_path: Path to event_spine.jsonl
        symbol: Symbol identifier
        allocation: AllocationResult with quantized posture fields
        source_event_id: Event ID that triggered this intent
        source_ts: Timestamp from source event

    Returns:
        (success, error) - success=True if intent emitted, else error explains why
        On validation failure, a veto is emitted instead (fail closed).
    """
    # Build payload with quantized fields (v0.2)
    payload = {
        "symbol": symbol,
        "direction": allocation.direction.value,
        "size_pct_q": allocation.size_pct_q,
        "size_pct_scale": allocation.size_pct_scale,
        "risk_cap": allocation.risk_cap.value,
        "rationale": allocation.rationale,
    }

    # Attach envelope (deterministic uncertainty bands)
    envelope = make_mock_envelope(
        intent_side=allocation.direction.value,
        confidence=allocation.entropy_factor,
        vetoed=False,
        size=allocation.size_pct_q / allocation.size_pct_scale,
    )
    payload["envelope"] = envelope.to_dict()

    payload = canonicalize_payload(payload, skip_unknown=True)

    # EMISSION BOUNDARY: Validate before write
    try:
        validate_router_intent(payload)
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
