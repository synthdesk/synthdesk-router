"""Schema + validation for router.intent and router.veto."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Optional, Tuple

# Schema version (constitutional)
# v0.3: Horizon and expiry are required fields. Intents without expiry are invalid.
INTENT_SCHEMA_VERSION = "v0.3"

# Supported schema versions for read-path (historical compatibility)
SUPPORTED_SCHEMA_VERSIONS = {None, "v0.2", "v0.3"}  # None = pre-versioned events

# Intent fields
DIRECTIONS = {"long", "short"}  # No "flat" - flat is veto, not intent

# Exit triggers (v0.3, constitutional)
# Defines what condition terminates the intent's validity
EXIT_TRIGGERS = {"time_stop", "regime_change", "price_stop", "manual"}

# Horizon consistency tolerance (minutes)
HORIZON_TOLERANCE_MINUTES = 1

# Maximum allowed horizon (prevents pathological values)
MAX_HORIZON_MINUTES = 24 * 60  # 24 hours

# Risk caps by format (constitutional)
# v0.2 posture layer: conservative caps only (no execution authority)
RISK_CAPS_V02 = {"zero", "low", "medium"}
# Legacy format: included for backward compatibility
RISK_CAPS_LEGACY = {"low", "normal", "high"}
# Combined for validation (union)
RISK_CAPS = RISK_CAPS_V02 | RISK_CAPS_LEGACY

# Veto reasons (frozen, exhaustive)
# Constitutional: changes require governance amendment
VETO_REASONS = {
    # System vetoes (infrastructure)
    "invariant_violation",
    "input_unavailable",
    "authority_gate",  # v0.1 blocks non-flat posture (added v0.2)
    # Edge-absent vetoes (abstention, not chaos claim)
    "regime_unresolved",
    "no_edge",  # chop regime: no directional signal detected
    "confidence_too_low",  # confidence < threshold
    # Danger vetoes (should correlate with chaos)
    "regime_volatile",  # high_vol regime: excess risk
}

# Quantization constants (v0.2)
SIZE_PCT_SCALE = 10000  # Denominator for quantized sizing


def _parse_iso_timestamp(ts_str: str) -> Optional[datetime]:
    """Parse ISO8601 timestamp string to datetime."""
    if not ts_str or not isinstance(ts_str, str):
        return None
    try:
        normalized = ts_str.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized)
    except (ValueError, TypeError):
        return None


def _validate_v03_expiry_fields(payload: dict, source_ts: Optional[str] = None) -> None:
    """
    Validate v0.3 required expiry fields (WRITE PATH ONLY).

    Constitutional rule: Intents without expiry are invalid for new emissions.

    Args:
        payload: Intent payload dict
        source_ts: Source timestamp (for horizon consistency check)

    Raises:
        ValueError: If any v0.3 required field is missing or invalid
    """
    schema_version = payload.get("schema_version")
    if schema_version != INTENT_SCHEMA_VERSION:
        raise ValueError(
            f"schema_version must be '{INTENT_SCHEMA_VERSION}', got: {schema_version}"
        )

    valid_until_ts = payload.get("valid_until_ts")
    if not valid_until_ts or not isinstance(valid_until_ts, str):
        raise ValueError("valid_until_ts is required (ISO8601 timestamp)")
    expiry_dt = _parse_iso_timestamp(valid_until_ts)
    if expiry_dt is None:
        raise ValueError(f"valid_until_ts is not valid ISO8601: {valid_until_ts}")

    horizon_minutes = payload.get("horizon_minutes")
    if not isinstance(horizon_minutes, int) or horizon_minutes <= 0:
        raise ValueError("horizon_minutes must be positive integer")

    # Prevent pathological horizons
    if horizon_minutes > MAX_HORIZON_MINUTES:
        raise ValueError(
            f"horizon_minutes ({horizon_minutes}) exceeds maximum ({MAX_HORIZON_MINUTES})"
        )

    exit_trigger = payload.get("exit_trigger")
    if exit_trigger not in EXIT_TRIGGERS:
        raise ValueError(f"exit_trigger must be one of {sorted(EXIT_TRIGGERS)}, got: {exit_trigger}")

    entry_basis_event_id = payload.get("entry_basis_event_id")
    if not entry_basis_event_id or not isinstance(entry_basis_event_id, str):
        raise ValueError("entry_basis_event_id is required")

    # Horizon consistency check: valid_until_ts - source_ts must match horizon_minutes
    if source_ts:
        source_dt = _parse_iso_timestamp(source_ts)
        if source_dt and expiry_dt:
            # Invariant: expiry must be in the future relative to source
            if expiry_dt <= source_dt:
                raise ValueError(
                    f"valid_until_ts ({valid_until_ts}) must be after source_ts ({source_ts})"
                )

            computed_horizon = (expiry_dt - source_dt).total_seconds() / 60
            drift = abs(computed_horizon - horizon_minutes)
            if drift > HORIZON_TOLERANCE_MINUTES:
                raise ValueError(
                    f"horizon drift: valid_until_ts implies {computed_horizon:.1f}m "
                    f"but horizon_minutes={horizon_minutes} (tolerance={HORIZON_TOLERANCE_MINUTES}m)"
                )

            # Invariant: computed horizon must not exceed max
            if computed_horizon > MAX_HORIZON_MINUTES:
                raise ValueError(
                    f"computed horizon ({computed_horizon:.1f}m) exceeds maximum ({MAX_HORIZON_MINUTES}m)"
                )


def _validate_common_intent_fields(payload: dict) -> None:
    """
    Validate fields common to all intent schema versions.

    Args:
        payload: Intent payload dict

    Raises:
        ValueError: If any common field is invalid
    """
    direction = payload.get("direction")
    if direction not in DIRECTIONS:
        raise ValueError(f"invalid direction: {direction} (must be long/short, not flat)")

    # Detect format: v0.2 quantized vs legacy float
    has_size_pct_q = "size_pct_q" in payload
    has_size_pct_scale = "size_pct_scale" in payload
    has_size_pct = "size_pct" in payload

    # CRITICAL: Reject mixed-format payloads
    if has_size_pct and (has_size_pct_q or has_size_pct_scale):
        raise ValueError("mixed format: cannot have both size_pct and size_pct_q/size_pct_scale")

    if has_size_pct_q or has_size_pct_scale:
        # v0.2 quantized format - MUST have both fields
        if not has_size_pct_q:
            raise ValueError("v0.2 format requires size_pct_q")
        if not has_size_pct_scale:
            raise ValueError("v0.2 format requires size_pct_scale")

        size_pct_q = payload.get("size_pct_q")
        if not isinstance(size_pct_q, int) or size_pct_q < 0:
            raise ValueError("size_pct_q must be non-negative integer")

        size_pct_scale = payload.get("size_pct_scale")
        if size_pct_scale != SIZE_PCT_SCALE:
            raise ValueError(f"size_pct_scale must be {SIZE_PCT_SCALE}")

        # v0.2 format: enforce v0.2 risk caps
        risk_cap = payload.get("risk_cap")
        if risk_cap not in RISK_CAPS_V02:
            raise ValueError(f"v0.2 risk_cap must be one of {sorted(RISK_CAPS_V02)}, got: {risk_cap}")

        # v0.2: non-flat intent with zero size is invalid (should be veto)
        if size_pct_q == 0 and direction in DIRECTIONS:
            raise ValueError("v0.2: size_pct_q=0 with non-flat direction is invalid (should be veto)")

    else:
        # Legacy float format
        size_pct = payload.get("size_pct")
        if size_pct is None:
            raise ValueError("legacy format requires size_pct")
        if not isinstance(size_pct, (int, float)) or isinstance(size_pct, bool):
            raise ValueError("size_pct must be numeric")
        if not math.isfinite(size_pct) or size_pct < 0.0:
            raise ValueError("size_pct must be finite and non-negative")

        # Legacy format: enforce legacy risk caps
        risk_cap = payload.get("risk_cap")
        if risk_cap not in RISK_CAPS_LEGACY:
            raise ValueError(f"legacy risk_cap must be one of {sorted(RISK_CAPS_LEGACY)}, got: {risk_cap}")

    rationale = payload.get("rationale")
    if not isinstance(rationale, list) or not all(isinstance(x, str) for x in rationale):
        raise ValueError("rationale must be list of strings")
    if len(rationale) == 0:
        raise ValueError("rationale must not be empty")


def validate_router_intent(payload: Any, source_ts: Optional[str] = None) -> None:
    """
    Validate router.intent payload for WRITE PATH (new emissions).

    v0.3 (current): Requires expiry fields (valid_until_ts, horizon_minutes, exit_trigger, entry_basis_event_id).
    Supports both legacy (size_pct float) and v0.2+ (size_pct_q integer) sizing formats.
    Mixed-format payloads are REJECTED (must be exactly one format).

    USE THIS FOR: Validating payloads before writing to spine.
    DO NOT USE FOR: Reading historical events (use validate_router_intent_read instead).

    Args:
        payload: Intent payload dict
        source_ts: Source timestamp for horizon consistency check (required for full validation)

    Raises:
        ValueError: If payload is invalid
    """
    if not isinstance(payload, dict):
        raise ValueError("payload must be dict")

    # v0.3 CONSTITUTIONAL GATE: Intents without expiry are invalid for writes
    _validate_v03_expiry_fields(payload, source_ts)

    # Validate common fields
    _validate_common_intent_fields(payload)

    # v0.3: Envelope horizon must match intent horizon
    envelope = payload.get("envelope")
    if envelope and isinstance(envelope, dict):
        envelope_horizon = envelope.get("horizon_minutes")
        intent_horizon = payload.get("horizon_minutes")
        if envelope_horizon is not None and intent_horizon is not None:
            if envelope_horizon != intent_horizon:
                raise ValueError(
                    f"envelope.horizon_minutes ({envelope_horizon}) must match "
                    f"intent.horizon_minutes ({intent_horizon})"
                )


def validate_router_intent_read(payload: Any) -> None:
    """
    Validate router.intent payload for READ PATH (historical events).

    Dispatches by schema_version to allow reading pre-v0.3 events.
    Does NOT require v0.3 expiry fields for older schema versions.

    USE THIS FOR: Reading/replaying historical events from spine.
    DO NOT USE FOR: Validating new emissions (use validate_router_intent instead).

    Args:
        payload: Intent payload dict

    Raises:
        ValueError: If payload is invalid for its schema version
    """
    if not isinstance(payload, dict):
        raise ValueError("payload must be dict")

    schema_version = payload.get("schema_version")

    # Check if schema version is supported
    if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        raise ValueError(f"unsupported schema_version: {schema_version}")

    # Validate common fields (all versions)
    _validate_common_intent_fields(payload)

    # Version-specific validation
    if schema_version == "v0.3":
        # v0.3: Validate expiry fields exist (but skip consistency check without source_ts)
        _validate_v03_expiry_fields(payload, source_ts=None)

        # v0.3: Envelope horizon must match intent horizon
        envelope = payload.get("envelope")
        if envelope and isinstance(envelope, dict):
            envelope_horizon = envelope.get("horizon_minutes")
            intent_horizon = payload.get("horizon_minutes")
            if envelope_horizon is not None and intent_horizon is not None:
                if envelope_horizon != intent_horizon:
                    raise ValueError(
                        f"envelope.horizon_minutes ({envelope_horizon}) must match "
                        f"intent.horizon_minutes ({intent_horizon})"
                    )
    # v0.2 and pre-versioned: no expiry fields required (historical compatibility)


# Backwards compatibility alias
def validate_router_intent_write(payload: Any, source_ts: Optional[str] = None) -> None:
    """Alias for validate_router_intent (write path)."""
    return validate_router_intent(payload, source_ts)


def validate_router_veto(payload: Any) -> None:
    """Validate router.veto payload."""
    if not isinstance(payload, dict):
        raise ValueError("payload must be dict")

    symbol = payload.get("symbol")
    if not isinstance(symbol, str) or not symbol:
        raise ValueError("symbol invalid")

    veto_reason = payload.get("veto_reason")
    if veto_reason not in VETO_REASONS:
        raise ValueError("invalid veto_reason")


# -----------------------------------------------------------------------------
# Consumer boundary functions (execution/analysis)
# -----------------------------------------------------------------------------


def is_intent_expired(payload: dict, now_ts: str) -> bool:
    """
    Check if intent has expired (CONSUMER BOUNDARY).

    Constitutional rule: Expired intents are non-actionable.
    Consumers MUST call this before acting on any intent.

    Args:
        payload: Intent payload dict (must have valid_until_ts for v0.3)
        now_ts: Current timestamp (ISO8601)

    Returns:
        True if intent is expired (now_ts >= valid_until_ts), False otherwise.
        For pre-v0.3 intents without valid_until_ts, returns False (legacy compatibility).
    """
    valid_until_ts = payload.get("valid_until_ts")
    if not valid_until_ts:
        # Pre-v0.3 intent: no expiry field, treat as not expired (legacy)
        return False

    expiry_dt = _parse_iso_timestamp(valid_until_ts)
    now_dt = _parse_iso_timestamp(now_ts)

    if expiry_dt is None or now_dt is None:
        # Parse failure: fail safe by treating as expired
        return True

    return now_dt >= expiry_dt


def is_intent_actionable(payload: dict, now_ts: str) -> bool:
    """
    Check if intent is actionable (CONSUMER BOUNDARY).

    An intent is actionable if:
    1. It is not expired (now_ts < valid_until_ts)
    2. It passes read validation

    This is the canonical check consumers should use before execution.

    Args:
        payload: Intent payload dict
        now_ts: Current timestamp (ISO8601)

    Returns:
        True if intent can be acted upon, False otherwise.
    """
    # Check expiry first (cheap)
    if is_intent_expired(payload, now_ts):
        return False

    # Check structural validity
    try:
        validate_router_intent_read(payload)
        return True
    except ValueError:
        return False


def count_horizon_violations(intents: list, decision_ts_fn) -> int:
    """
    Count intents acted upon after expiry (REGRESSION METRIC).

    Constitutional invariant: This number MUST be zero for v0.3 paths.
    Non-zero indicates executor or analysis bug.

    Args:
        intents: List of intent payloads
        decision_ts_fn: Function (intent) -> decision_ts string
                        Returns the timestamp when the intent was acted upon.
                        For analysis: typically the trade entry time.
                        For executor: typically now().

    Returns:
        Count of intents where decision_ts >= valid_until_ts.
        Pre-v0.3 intents (no valid_until_ts) are excluded from count.

    Example:
        violations = count_horizon_violations(
            intents=spine_intents,
            decision_ts_fn=lambda i: i.get("decision_ts") or i.get("source_ts")
        )
        assert violations == 0, f"horizon violations: {violations}"
    """
    count = 0
    for intent in intents:
        # Skip pre-v0.3 intents (no expiry to violate)
        if not intent.get("valid_until_ts"):
            continue

        decision_ts = decision_ts_fn(intent)
        if decision_ts and is_intent_expired(intent, decision_ts):
            count += 1

    return count
