"""
synthdesk_spine

Event spine substrate: versioned envelopes, typed payloads, deterministic consumption.

This package has no runtime authority. It is pure contract + tooling.
"""

__version__ = "0.1.0"

from .envelope import EventEnvelope, EVENT_ENVELOPE_VERSION
from .consumer import SpineConsumer
from .serialization import payload_to_dict, dict_to_payload
from .errors import (
    SpineError,
    SpineCorruptionError,
    UnsupportedSchemaVersion,
    PayloadValidationError,
)
from .invariants import Invariant, InvariantRegistry, SeverityType, ActionType
from .canonicalize import (
    canonicalize_float,
    canonicalize_field,
    canonicalize_payload,
    validate_finite,
    get_field_precision,
    get_registry_info,
    PRECISION_REGISTRY,
    REGISTRY_VERSION,
    FPDET_SPEC_VERSION,
)
from .symbols import (
    SymbolRegistry,
    SUPPORTED_SYMBOLS,
    DEFAULT_SYMBOLS,
    ANCHOR_SYMBOL,
    validate_symbols,
)

__all__ = [
    "__version__",
    "EventEnvelope",
    "EVENT_ENVELOPE_VERSION",
    "SpineConsumer",
    "payload_to_dict",
    "dict_to_payload",
    "SpineError",
    "SpineCorruptionError",
    "UnsupportedSchemaVersion",
    "PayloadValidationError",
    "Invariant",
    "InvariantRegistry",
    "SeverityType",
    "ActionType",
    "canonicalize_float",
    "canonicalize_field",
    "canonicalize_payload",
    "validate_finite",
    "get_field_precision",
    "get_registry_info",
    "PRECISION_REGISTRY",
    "REGISTRY_VERSION",
    "FPDET_SPEC_VERSION",
    "SymbolRegistry",
    "SUPPORTED_SYMBOLS",
    "DEFAULT_SYMBOLS",
    "ANCHOR_SYMBOL",
    "validate_symbols",
]
