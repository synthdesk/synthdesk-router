"""
Speech emission v1 (EVT-0 admissible).

Constructs a router.speech.v1 event if all invariants are met.
No side effects; returns (None, reason) if invariants fail.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from emission.speech_counters import (
    INVALID_TIMESTAMP,
    MISSING_ENVELOPE,
    MISSING_HORIZON,
    MISSING_PROBS,
    OTHER_INVARIANT_FAIL,
)
from synthdesk_spine.event_types import ROUTER_SPEECH_V1


def _parse_ts(ts: str) -> Optional[datetime]:
    if not isinstance(ts, str):
        return None
    try:
        normalized = ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def _as_float(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def emit_speech_v1(
    event: Dict[str, Any],
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    if event.get("event_type") == "router.speech.v1":
        return None, OTHER_INVARIANT_FAIL

    payload = event.get("payload")
    if not isinstance(payload, dict):
        return None, MISSING_ENVELOPE

    symbol = payload.get("symbol")
    if not isinstance(symbol, str):
        return None, OTHER_INVARIANT_FAIL

    envelope = payload.get("envelope")
    if not isinstance(envelope, dict):
        return None, MISSING_ENVELOPE

    p_long = _as_float(envelope.get("p_long"))
    p_short = _as_float(envelope.get("p_short"))
    p_flat = _as_float(envelope.get("p_flat"))
    if p_long is None or p_short is None or p_flat is None:
        return None, MISSING_PROBS

    ts_str = event.get("timestamp") or event.get("source_ts")
    ts = _parse_ts(ts_str) if isinstance(ts_str, str) else None
    if ts is None:
        return None, INVALID_TIMESTAMP

    horizon_minutes = _as_float(envelope.get("horizon_minutes"))
    valid_until_ts = envelope.get("valid_until_ts") or payload.get("valid_until_ts")

    if horizon_minutes is not None and horizon_minutes <= 0:
        return None, MISSING_HORIZON

    if horizon_minutes is None and isinstance(valid_until_ts, str):
        valid_ts = _parse_ts(valid_until_ts)
        if valid_ts is None:
            return None, MISSING_HORIZON
        if (valid_ts - ts).total_seconds() <= 0:
            return None, MISSING_HORIZON
    elif horizon_minutes is None:
        return None, MISSING_HORIZON

    speech_payload: Dict[str, Any] = {
        "envelope": {
            "p_long": p_long,
            "p_short": p_short,
            "p_flat": p_flat,
        }
    }
    if horizon_minutes is not None:
        speech_payload["horizon_minutes"] = horizon_minutes
    else:
        speech_payload["valid_until_ts"] = valid_until_ts

    return (
        {
            "event_type": ROUTER_SPEECH_V1,
            "version": "1.0",
            "timestamp": ts_str,
            "symbol": symbol,
            "payload": speech_payload,
        },
        None,
    )
