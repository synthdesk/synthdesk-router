"""
Binding utilities for envelope ↔ intent alignment.

Hard rules:
- Bind by source_event_id only (no timestamp bucketing).
- No collisions (duplicate source_event_id).
- No silent drops (missing bindings are explicit failures).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Iterable, List

BINDING_SCHEMA_VERSION = "BINDING_V0_1"


class BindingError(RuntimeError):
    """Base class for binding errors with attached report."""

    def __init__(self, message: str, report: Dict[str, Any]) -> None:
        super().__init__(message)
        self.report = report


class BindingCollisionError(BindingError):
    """Raised when multiple rows share a source_event_id."""


class MissingBindingError(BindingError):
    """Raised when an envelope row cannot bind to any intent."""


def hash_bound_ids(bound_ids: List[str]) -> str:
    """Hash ordered bound_ids list for auditability."""
    payload = json.dumps(bound_ids, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_envelope_bindings(
    intents: Iterable[Dict[str, Any]],
    envelopes: Iterable[Dict[str, Any]],
    *,
    require_symbol_match: bool = True,
) -> Dict[str, Any]:
    """
    Validate that every envelope row binds to exactly one intent.

    Returns binding report dict. Raises on collisions or missing bindings.
    """
    intent_list = list(intents)
    envelope_list = list(envelopes)

    intent_index: Dict[str, Dict[str, Any]] = {}
    collisions: List[Dict[str, Any]] = []
    missing: Dict[str, int] = {}

    for intent in intent_list:
        source_event_id = intent.get("source_event_id")
        if not source_event_id:
            missing["__missing_source_event_id__"] = missing.get("__missing_source_event_id__", 0) + 1
            continue
        if source_event_id in intent_index:
            collisions.append({
                "source_event_id": source_event_id,
                "first_intent_id": intent_index[source_event_id].get("intent_id"),
                "second_intent_id": intent.get("intent_id"),
                "first_symbol": intent_index[source_event_id].get("symbol"),
                "second_symbol": intent.get("symbol"),
                "collision": "intent_duplicate",
            })
            continue
        intent_index[source_event_id] = intent

    bound_ids: List[str] = []
    envelope_seen: Dict[str, int] = {}

    for env in envelope_list:
        source_event_id = env.get("source_event_id")
        if not source_event_id:
            missing["__missing_source_event_id__"] = missing.get("__missing_source_event_id__", 0) + 1
            continue

        envelope_seen[source_event_id] = envelope_seen.get(source_event_id, 0) + 1
        if envelope_seen[source_event_id] > 1:
            collisions.append({
                "source_event_id": source_event_id,
                "collision": "envelope_duplicate",
            })
            continue

        intent = intent_index.get(source_event_id)
        if intent is None:
            missing[source_event_id] = missing.get(source_event_id, 0) + 1
            continue

        if require_symbol_match:
            env_symbol = env.get("symbol")
            intent_symbol = intent.get("symbol")
            if env_symbol and intent_symbol and env_symbol != intent_symbol:
                collisions.append({
                    "source_event_id": source_event_id,
                    "collision": "symbol_mismatch",
                    "intent_symbol": intent_symbol,
                    "envelope_symbol": env_symbol,
                })
                continue

        bound_ids.append(source_event_id)

    report = {
        "schema_version": BINDING_SCHEMA_VERSION,
        "n_rows": len(envelope_list),
        "n_bound": len(bound_ids),
        "n_missing": sum(missing.values()) if missing else 0,
        "n_collisions": len(collisions),
        "bound_ids_sha256": hash_bound_ids(bound_ids),
    }

    if missing:
        report["missing_ids"] = missing
    if collisions:
        report["collisions"] = collisions

    if collisions:
        raise BindingCollisionError("binding collisions detected", report)
    if missing:
        raise MissingBindingError("missing bindings detected", report)

    return report
