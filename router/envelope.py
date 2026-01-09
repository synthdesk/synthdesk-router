# packages/router/router/envelope.py
"""
Mock Envelope Adapter - Deterministic uncertainty bands.

Locks the "enveloped intent contract" now. Later replaceable with
real E.pt / MC output from quant_cortex with minimal surgery.

Contract:
- Deterministic given intent fields (no time, no randomness)
- Schema stable + forward-compatible with quant_cortex envelope schema
- Zero Modal/GPU dependency
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict


@dataclass(frozen=True)
class Envelope:
    """
    Uncertainty envelope for router intent.

    Minimal v0: direction probs + sizing bands.
    Forward-compatible with quant_cortex tensor schema (8 channels).
    """

    # Direction probabilities (sum to 1.0 when not vetoed)
    p_flat: float
    p_long: float
    p_short: float
    p_vetoed: float

    # Deterministic sizing band (uncertainty-modulated)
    size_min: float
    size_max: float

    # Provenance for future parity with quant_cortex
    kernel: str = "mock_v0"
    version: str = "0.0.1"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _clamp01(x: float) -> float:
    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return x


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default


def make_mock_envelope(
    *,
    intent_side: str,
    confidence: float,
    vetoed: bool,
    size: float,
) -> Envelope:
    """
    Deterministic mock envelope.

    Args:
        intent_side: "LONG", "SHORT", or "FLAT"
        confidence: [0, 1] - higher = more certain
        vetoed: If True, forces p_vetoed=1.0 and collapses sizing band
        size: Base size (will be banded by confidence)

    Returns:
        Envelope with deterministic probability distribution and sizing bands.

    Behavior:
    - Uses confidence to allocate p_flat vs p_dir
    - Uses side to split long/short
    - Veto flag forces p_vetoed=1.0 and collapses sizing band to zero
    """
    c = _clamp01(_safe_float(confidence, 0.0))

    if vetoed:
        # Veto dominates: downstream systems treat this as a hard state
        return Envelope(
            p_flat=0.0,
            p_long=0.0,
            p_short=0.0,
            p_vetoed=1.0,
            size_min=0.0,
            size_max=0.0,
        )

    # Baseline: more confidence => less flat
    # Keep some minimum flatness so the mock always expresses uncertainty
    p_flat = _clamp01(0.65 - 0.50 * c)  # confidence 0 -> 0.65 flat, confidence 1 -> 0.15 flat
    p_dir = _clamp01(1.0 - p_flat)

    side = (intent_side or "FLAT").upper()
    if side == "LONG":
        p_long, p_short = p_dir, 0.0
    elif side == "SHORT":
        p_long, p_short = 0.0, p_dir
    else:
        # FLAT or unknown
        p_long, p_short = 0.0, 0.0
        p_flat = 1.0

    # Sizing band widens when confidence is low
    # Keep it deterministic and bounded
    s = abs(_safe_float(size, 0.0))
    band = 0.20 + 0.60 * (1.0 - c)  # c=1 -> 0.20 band, c=0 -> 0.80 band
    size_min = max(0.0, s * (1.0 - band))
    size_max = s * (1.0 + band)

    return Envelope(
        p_flat=_clamp01(p_flat),
        p_long=_clamp01(p_long),
        p_short=_clamp01(p_short),
        p_vetoed=0.0,
        size_min=size_min,
        size_max=size_max,
    )
