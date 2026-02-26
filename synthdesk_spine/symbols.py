"""
Symbol Registry - Append-only symbol definitions.

Constitutional constraint: symbols may only be ADDED, never removed.
Like VetoReason, this is a frozen registry that ensures backward compatibility.

Usage:
    from synthdesk_spine.symbols import SymbolRegistry, SUPPORTED_SYMBOLS

    # Check if symbol is valid
    if symbol in SUPPORTED_SYMBOLS:
        ...

    # Get all symbols
    for symbol in SymbolRegistry.all():
        ...
"""

from enum import Enum
from typing import FrozenSet, List


class SymbolRegistry(str, Enum):
    """
    Append-only symbol registry.

    CONSTITUTIONAL: Symbols may only be ADDED, never removed.

    Each symbol represents a tradeable asset pair. The naming convention
    is <BASE><QUOTE> (e.g., BTCUSDT = Bitcoin quoted in USDT).

    When adding new symbols:
    1. Add a new class attribute (SYMBOLQUOTE = "SYMBOLQUOTE")
    2. Never modify or remove existing symbols
    3. Update SUPPORTED_SYMBOLS frozenset via all() method
    """

    # Phase 1 symbols (v0.2.0)
    BTCUSDT = "BTCUSDT"
    ETHUSDT = "ETHUSDT"
    SOLUSDT = "SOLUSDT"

    # Phase 2 symbols (v0.3.0) - Multi-asset expansion
    XRPUSDT = "XRPUSDT"
    BNBUSDT = "BNBUSDT"
    ADAUSDT = "ADAUSDT"
    DOGEUSDT = "DOGEUSDT"
    AVAXUSDT = "AVAXUSDT"
    LINKUSDT = "LINKUSDT"
    MATICUSDT = "MATICUSDT"

    # Future symbols (append here, never remove above)

    @classmethod
    def all(cls) -> List[str]:
        """Return all registered symbols as strings."""
        return [member.value for member in cls]

    @classmethod
    def is_valid(cls, symbol: str) -> bool:
        """Check if symbol is in the registry."""
        return symbol in {member.value for member in cls}


# Frozen set for O(1) membership testing
SUPPORTED_SYMBOLS: FrozenSet[str] = frozenset(SymbolRegistry.all())

# Backward-compatibility alias for legacy spectral observers.
SPECTRAL_ELIGIBLE: FrozenSet[str] = SUPPORTED_SYMBOLS

# Default symbols for listener/router initialization
# Phase 2: expanded to 10 symbols for multi-asset evidence generation
DEFAULT_SYMBOLS: List[str] = [
    SymbolRegistry.BTCUSDT.value,
    SymbolRegistry.ETHUSDT.value,
    SymbolRegistry.SOLUSDT.value,
    SymbolRegistry.XRPUSDT.value,
    SymbolRegistry.BNBUSDT.value,
    SymbolRegistry.ADAUSDT.value,
    SymbolRegistry.DOGEUSDT.value,
    SymbolRegistry.AVAXUSDT.value,
    SymbolRegistry.LINKUSDT.value,
    SymbolRegistry.MATICUSDT.value,
]

# Anchor symbol for correlation calculations
# The first symbol in DEFAULT_SYMBOLS is the correlation anchor
ANCHOR_SYMBOL: str = DEFAULT_SYMBOLS[0]


def validate_symbols(symbols: List[str]) -> List[str]:
    """
    Validate a list of symbols against the registry.

    Args:
        symbols: List of symbol strings to validate

    Returns:
        List of valid symbols (invalid ones silently dropped)

    Raises:
        ValueError: If no valid symbols provided
    """
    valid = [s for s in symbols if SymbolRegistry.is_valid(s)]
    if not valid:
        raise ValueError(
            f"No valid symbols provided. "
            f"Supported: {sorted(SUPPORTED_SYMBOLS)}"
        )
    return valid
