"""
Router state management.

Minimal, explicit state derived only from events.
Reconstructible via replay.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict, Optional

from synthdesk_spine.event_types import (
    GOVERNANCE_EVIDENCE_EXPIRED,
    INVARIANT_DRIFT_CRITICAL,
    INVARIANT_DRIFT_WARNING,
    INVARIANT_VIOLATION,
    LISTENER_CRASH,
    LISTENER_START,
    MARKET_REGIME,
    MARKET_REGIME_CHANGE,
    SPECTRAL_EMIT,
)

if TYPE_CHECKING:
    from router.allocator import AllocationResult


# =============================================================================
# DOCTRINE: EVIDENCE_NONACTIONABILITY
# Router MUST NOT read, branch on, or propagate evidence confidence fields.
# These fields are observer-only, forensic-only.
# =============================================================================

# Fields that the router must ignore (strip from payloads)
_EVIDENCE_CONFIDENCE_FIELDS = frozenset({
    "evidence_confidence",
    "confidence_source",
    "confidence_components",
})

# =============================================================================
# RISK ENVELOPE: Effective rank staleness bound
# If spectral.emit is older than this, treat as missing (fail-closed → rank 1)
# =============================================================================
MAX_EFFECTIVE_RANK_STALENESS_MS = 5 * 60 * 1000  # 5 minutes


def _sanitize_payload(payload: Dict) -> Dict:
    """
    Strip evidence confidence fields from event payload.

    DOCTRINE: EVIDENCE_NONACTIONABILITY
    The router must not read or branch on these fields.
    This is a defense-in-depth measure; perception events
    are already filtered by ALLOWED_EVENT_TYPES.

    Args:
        payload: Event payload dict

    Returns:
        Payload with evidence confidence fields removed
    """
    for field in _EVIDENCE_CONFIDENCE_FIELDS:
        payload.pop(field, None)
    return payload


class RouterState:
    """
    Router-local state (ephemeral, process-scoped).

    State is:
    - Derived only from events
    - Reconstructible via replay
    - Never persisted
    - Never written back upstream
    """

    def __init__(self, authority_epoch_ts: Optional[str] = None):
        """
        Initialize empty router state.

        Args:
            authority_epoch_ts: ISO timestamp marking authority epoch start.
                If provided, invariant violations before this timestamp are
                ignored (they belong to a prior epoch and cannot demote the
                current authority binding). If None, all violations count.
        """
        self.symbols: Dict[str, Dict] = {}
        self.system = {
            "listener_alive": False,
            "last_listener_event_ts": None,
            # Violation memory (passive - authority plane decides demotion)
            "last_violation_ts": None,
            "last_violation_severity": None,
            "last_violation_symbol": None,
        }
        self._authority_epoch_ts = authority_epoch_ts
        # Degraded symbols: non-critical violations cause posture degradation, not authority demotion
        self._degraded_symbols: set[str] = set()
        # Effective rank cache for risk envelope (from spectral.emit, read-only)
        self._effective_rank: Dict[str, int] = {}
        self._effective_rank_ts_ms: Dict[str, int] = {}

    def update_from_event(self, event: Dict) -> None:
        """
        Update state from spine event.

        Args:
            event: Event dict from spine
        """
        event_type = event.get("event_type")
        payload = event.get("payload")
        timestamp = event.get("timestamp")

        if not isinstance(event_type, str) or not isinstance(payload, dict):
            return

        # DOCTRINE: EVIDENCE_NONACTIONABILITY
        # Strip evidence confidence fields before processing
        payload = _sanitize_payload(payload)

        # Lifecycle events
        if event_type == LISTENER_START:
            self.system["listener_alive"] = True
            self.system["last_listener_event_ts"] = timestamp

        elif event_type == LISTENER_CRASH:
            self.system["listener_alive"] = False
            self.system["last_listener_event_ts"] = timestamp

        # Invariant violations (epoch-scoped, passive recording)
        # State records ALL violations within epoch. Authority plane decides demotion policy.
        elif event_type in (INVARIANT_VIOLATION, INVARIANT_DRIFT_WARNING, INVARIANT_DRIFT_CRITICAL):
            # Only record violations within the current authority epoch.
            if self._authority_epoch_ts is None or timestamp >= self._authority_epoch_ts:
                severity = payload.get("severity", "warning")
                symbol = payload.get("symbol") or payload.get("details", {}).get("symbol")
                if not symbol:
                    symbol = payload.get("details", {}).get("observed", {}).get("missing_pairs", [None])[0]

                self.system["last_violation_ts"] = timestamp
                self.system["last_violation_severity"] = severity
                self.system["last_violation_symbol"] = symbol

                if severity != "critical" and symbol:
                    self._degraded_symbols.add(symbol)

        elif event_type == GOVERNANCE_EVIDENCE_EXPIRED:
            if self._authority_epoch_ts is None or timestamp >= self._authority_epoch_ts:
                severity = payload.get("severity", "critical")
                symbol = payload.get("descriptor_id") or "governance"
                self.system["last_violation_ts"] = timestamp
                self.system["last_violation_severity"] = severity
                self.system["last_violation_symbol"] = symbol

        # Market regime updates (also clears degraded status - auto-recovery)
        elif event_type == MARKET_REGIME:
            symbol = payload.get("symbol")
            regime = payload.get("regime")
            confidence = payload.get("confidence")
            if isinstance(symbol, str) and isinstance(regime, str):
                if symbol not in self.symbols:
                    self.symbols[symbol] = {}
                self.symbols[symbol]["regime"] = regime
                self.symbols[symbol]["last_regime_ts"] = timestamp
                # Store regime calibration for entropy computation
                if confidence is not None:
                    try:
                        self.symbols[symbol]["regime_confidence"] = float(confidence)
                    except (TypeError, ValueError):
                        pass
                # Store regime metrics for momentum kernel (z_mean is directional signal)
                metrics = payload.get("metrics")
                if isinstance(metrics, dict):
                    z_mean = metrics.get("z_mean")
                    z_std = metrics.get("z_std")
                    if z_mean is not None:
                        try:
                            self.symbols[symbol]["z_mean"] = float(z_mean)
                        except (TypeError, ValueError):
                            pass
                    if z_std is not None:
                        try:
                            self.symbols[symbol]["z_std"] = float(z_std)
                        except (TypeError, ValueError):
                            pass
                    # Phase 1 telemetry: per-symbol rolling correlation (typically anchor-relative).
                    corr = (
                        metrics.get("rolling_correlation")
                        or metrics.get("rolling_corr")
                        or metrics.get("corr")
                        or metrics.get("correlation")
                        or metrics.get("corr_to_anchor")
                    )
                    if corr is not None:
                        try:
                            self.symbols[symbol]["rolling_correlation"] = float(corr)
                        except (TypeError, ValueError):
                            pass
                # Extract range_norm from phase1 primitives (H-019 validated, H-014 consumer)
                phase1 = payload.get("phase1")
                if isinstance(phase1, dict):
                    range_norm = phase1.get("range_norm")
                    if range_norm is not None:
                        try:
                            self.symbols[symbol]["range_norm"] = float(range_norm)
                        except (TypeError, ValueError):
                            pass
                # Auto-recovery: successful regime emission clears degraded status
                self._degraded_symbols.discard(symbol)

        elif event_type == MARKET_REGIME_CHANGE:
            symbol = payload.get("symbol")
            to_regime = payload.get("to")
            if isinstance(symbol, str) and isinstance(to_regime, str):
                if symbol not in self.symbols:
                    self.symbols[symbol] = {}
                self.symbols[symbol]["regime"] = to_regime
                self.symbols[symbol]["last_regime_ts"] = timestamp

        # Spectral emit: read-only cache for risk envelope (no authority)
        elif event_type == SPECTRAL_EMIT:
            symbol = payload.get("symbol") or payload.get("pair")
            effective_rank = payload.get("effective_rank")
            ts_ms = payload.get("ts_ms")
            if symbol is None or effective_rank is None or ts_ms is None:
                return  # Ignore malformed, do not crash
            try:
                self._effective_rank[symbol] = int(effective_rank)
                self._effective_rank_ts_ms[symbol] = int(ts_ms)
            except (TypeError, ValueError):
                pass  # Ignore bad types

    def get_regime(self, symbol: str) -> Optional[str]:
        """
        Get current regime for symbol.

        Args:
            symbol: Symbol identifier

        Returns:
            Regime string or None if unresolved
        """
        return self.symbols.get(symbol, {}).get("regime")

    def get_z_mean(self, symbol: str) -> Optional[float]:
        """
        Get z_mean (normalized drift) for symbol.

        This is the directional signal from regime classification.
        Positive z_mean indicates upward drift, negative indicates downward.

        Args:
            symbol: Symbol identifier

        Returns:
            z_mean float or None if unavailable
        """
        return self.symbols.get(symbol, {}).get("z_mean")

    def get_z_std(self, symbol: str) -> Optional[float]:
        """
        Get z_std (normalized volatility) for symbol.

        This indicates realized volatility relative to EWMA baseline.
        Values ~1.0 are normal; higher values indicate elevated volatility.

        Args:
            symbol: Symbol identifier

        Returns:
            z_std float or None if unavailable
        """
        return self.symbols.get(symbol, {}).get("z_std")

    def get_regime_confidence(self, symbol: str) -> Optional[float]:
        """
        Get regime confidence for symbol.

        Args:
            symbol: Symbol identifier

        Returns:
            Regime confidence float or None if unavailable
        """
        return self.symbols.get(symbol, {}).get("regime_confidence")

    def get_range_norm(self, symbol: str) -> Optional[float]:
        """
        Get range_norm (normalized volatility range) for symbol.

        This is the H-019 validated primitive for volatility persistence.
        Used by H-014 for defensive sizing (context-only, not directional).

        Args:
            symbol: Symbol identifier

        Returns:
            range_norm float or None if unavailable
        """
        return self.symbols.get(symbol, {}).get("range_norm")

    def get_effective_rank(self, symbol: str, now_ts_ms: int) -> Optional[int]:
        """
        Get effective_rank for symbol with staleness check.

        Used by risk envelope for regime-conditional caps.
        Fail-closed: returns None if missing or stale, caller must apply rank=1.

        Args:
            symbol: Symbol identifier
            now_ts_ms: Current timestamp in milliseconds

        Returns:
            effective_rank (1 or 2) or None if unavailable/stale
        """
        ts = self._effective_rank_ts_ms.get(symbol)
        if ts is None:
            return None
        if now_ts_ms - ts > MAX_EFFECTIVE_RANK_STALENESS_MS:
            return None
        return self._effective_rank.get(symbol)

    def get_last_intent(self, symbol: str) -> Optional[Dict]:
        """
        Get last emitted intent for symbol (for dedup).

        Args:
            symbol: Symbol identifier

        Returns:
            Last intent dict or None
        """
        return self.symbols.get(symbol, {}).get("last_intent")

    def set_last_intent(self, symbol: str, intent: Dict) -> None:
        """
        Record last emitted intent for symbol (for dedup, legacy).

        Clears last veto and allocation (XOR: intent or veto, not both).

        Args:
            symbol: Symbol identifier
            intent: Intent dict
        """
        if symbol not in self.symbols:
            self.symbols[symbol] = {}
        self.symbols[symbol]["last_intent"] = intent
        self.symbols[symbol]["last_allocation"] = None  # Clear v0.2 field
        self.symbols[symbol]["last_veto_reason"] = None

    def get_last_veto_reason(self, symbol: str) -> Optional[str]:
        """
        Get last emitted veto reason for symbol (for dedup).

        Args:
            symbol: Symbol identifier

        Returns:
            Last veto reason string or None
        """
        return self.symbols.get(symbol, {}).get("last_veto_reason")

    def set_last_veto_reason(self, symbol: str, veto_reason: str) -> None:
        """
        Record last emitted veto reason for symbol (for dedup).

        Clears last intent/allocation (XOR: intent or veto, not both).

        Args:
            symbol: Symbol identifier
            veto_reason: VetoReason.value string
        """
        if symbol not in self.symbols:
            self.symbols[symbol] = {}
        self.symbols[symbol]["last_veto_reason"] = veto_reason
        self.symbols[symbol]["last_intent"] = None
        self.symbols[symbol]["last_allocation"] = None

    def get_last_allocation(self, symbol: str) -> Optional["AllocationResult"]:
        """
        Get last emitted allocation for symbol (for dedup, v0.2).

        Args:
            symbol: Symbol identifier

        Returns:
            Last AllocationResult or None
        """
        return self.symbols.get(symbol, {}).get("last_allocation")

    def set_last_allocation(self, symbol: str, allocation: "AllocationResult") -> None:
        """
        Record last emitted allocation for symbol (for dedup, v0.2).

        Clears last veto (XOR: intent or veto, not both).

        Args:
            symbol: Symbol identifier
            allocation: AllocationResult from allocator
        """
        if symbol not in self.symbols:
            self.symbols[symbol] = {}
        self.symbols[symbol]["last_allocation"] = allocation
        self.symbols[symbol]["last_intent"] = None  # Clear legacy field
        self.symbols[symbol]["last_veto_reason"] = None

    def is_listener_alive(self) -> bool:
        """Check if listener is alive."""
        return self.system["listener_alive"]

    def get_last_violation(self) -> tuple[Optional[str], Optional[str], Optional[str]]:
        """
        Get last recorded violation info.

        Returns:
            (timestamp, severity, symbol) - all None if no violation recorded
        """
        return (
            self.system["last_violation_ts"],
            self.system["last_violation_severity"],
            self.system["last_violation_symbol"],
        )

    def has_violation(self) -> bool:
        """Check if any violation has been recorded (passive memory, not demotion decision)."""
        return self.system["last_violation_ts"] is not None

    def is_symbol_degraded(self, symbol: str) -> bool:
        """
        Check if symbol is in degraded state due to non-critical violation.

        Degraded symbols should receive flat/veto posture but do NOT
        trigger authority demotion. Auto-recovers on next healthy regime event.
        """
        return symbol in self._degraded_symbols

    def get_degraded_symbols(self) -> set[str]:
        """Get set of currently degraded symbols."""
        return self._degraded_symbols.copy()

    def clear_degraded(self, symbol: str) -> None:
        """
        Manually clear degraded status for a symbol.

        Normally auto-clears on market.regime event.
        """
        self._degraded_symbols.discard(symbol)
