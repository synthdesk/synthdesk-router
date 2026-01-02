"""
Router state management.

Minimal, explicit state derived only from events.
Reconstructible via replay.
"""

from typing import Dict, Optional


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
        Record last emitted intent for symbol (for dedup).

        Args:
            symbol: Symbol identifier
            intent: Intent dict
        """
        if symbol not in self.symbols:
            self.symbols[symbol] = {}
        self.symbols[symbol]["last_intent"] = intent

    def is_listener_alive(self) -> bool:
        """Check if listener is alive."""
        return self.system["listener_alive"]

    def is_violation_active(self) -> bool:
        """Check if invariant violation is active."""
        return self.system["violation_active"]
