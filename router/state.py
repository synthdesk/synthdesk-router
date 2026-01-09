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

    def __init__(self):
        """Initialize empty router state."""
        self.symbols: Dict[str, Dict] = {}
        self.system = {
            "listener_alive": False,
            "last_listener_event_ts": None,
            "violation_active": False,
            "last_violation_ts": None,
        }

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

        # Invariant violations
        elif event_type == "invariant.violation":
            self.system["violation_active"] = True
            self.system["last_violation_ts"] = timestamp

        # Market regime updates
        elif event_type == "market.regime":
            symbol = payload.get("symbol")
            regime = payload.get("regime")
            if isinstance(symbol, str) and isinstance(regime, str):
                if symbol not in self.symbols:
                    self.symbols[symbol] = {}
                self.symbols[symbol]["regime"] = regime
                self.symbols[symbol]["last_regime_ts"] = timestamp

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

    def is_violation_active(self) -> bool:
        """Check if invariant violation is active."""
        return self.system["violation_active"]
