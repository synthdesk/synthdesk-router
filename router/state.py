"""
Router state management.

Minimal, explicit state derived only from events.
Reconstructible via replay.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict, Optional

if TYPE_CHECKING:
    from router.allocator import AllocationResult


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

        # Lifecycle events
        if event_type == "listener.start":
            self.system["listener_alive"] = True
            self.system["last_listener_event_ts"] = timestamp

        elif event_type == "listener.crash":
            self.system["listener_alive"] = False
            self.system["last_listener_event_ts"] = timestamp

        # Invariant violations (epoch-scoped, passive recording)
        # State records ALL violations within epoch. Authority plane decides demotion policy.
        elif event_type == "invariant.violation":
            # Only record violations within the current authority epoch.
            # Pre-epoch violations cannot affect a later authority binding.
            if self._authority_epoch_ts is None or timestamp >= self._authority_epoch_ts:
                severity = payload.get("severity", "warning")
                symbol = payload.get("details", {}).get("observed", {}).get("missing_pairs", [None])[0]
                if not symbol:
                    # Try alternate payload shapes
                    symbol = payload.get("symbol")

                # Record the violation (passive memory)
                self.system["last_violation_ts"] = timestamp
                self.system["last_violation_severity"] = severity
                self.system["last_violation_symbol"] = symbol

                # Track degraded symbols for non-critical violations (posture gating)
                if severity != "critical" and symbol:
                    self._degraded_symbols.add(symbol)

        # Market regime updates (also clears degraded status - auto-recovery)
        elif event_type == "market.regime":
            symbol = payload.get("symbol")
            regime = payload.get("regime")
            confidence = payload.get("confidence")
            if isinstance(symbol, str) and isinstance(regime, str):
                if symbol not in self.symbols:
                    self.symbols[symbol] = {}
                self.symbols[symbol]["regime"] = regime
                self.symbols[symbol]["last_regime_ts"] = timestamp
                # Store regime confidence for entropy computation
                if confidence is not None:
                    try:
                        self.symbols[symbol]["regime_confidence"] = float(confidence)
                    except (TypeError, ValueError):
                        pass
                # Auto-recovery: successful regime emission clears degraded status
                self._degraded_symbols.discard(symbol)

        elif event_type == "market.regime_change":
            symbol = payload.get("symbol")
            to_regime = payload.get("to")
            if isinstance(symbol, str) and isinstance(to_regime, str):
                if symbol not in self.symbols:
                    self.symbols[symbol] = {}
                self.symbols[symbol]["regime"] = to_regime
                self.symbols[symbol]["last_regime_ts"] = timestamp

    def get_regime(self, symbol: str) -> Optional[str]:
        """
        Get current regime for symbol.

        Args:
            symbol: Symbol identifier

        Returns:
            Regime string or None if unresolved
        """
        return self.symbols.get(symbol, {}).get("regime")

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
