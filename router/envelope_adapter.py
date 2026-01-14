"""
envelope_adapter.py

Attaches envelope provenance to router intent events.

This is the bridge between the tensor vault and live router emissions.
For now, it attaches metadata only. Real MC envelope probabilities
(p_long, p_short, etc.) will be bound in EVT-1 when the MC kernel
is wired locally.

Constitutional staging: metadata-only is correct for EVT-0.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


class VaultIndex:
    """
    Index of available vault envelopes by time bucket.

    Time buckets are hour-granularity: "2026-01-11T14" covers 14:00-14:59.
    """

    def __init__(self):
        self._index: Dict[str, Dict[str, Any]] = {}

    def register(self, vault_meta: Dict[str, Any]) -> None:
        """
        Register a vault envelope in the index.

        Args:
            vault_meta: The meta.json contents from a verified vault
        """
        bounds = vault_meta.get("slice_bounds", {})
        from_ts = bounds.get("from_ts", "")
        to_ts = bounds.get("to_ts", "")

        if not from_ts:
            return

        # Extract hour bucket from from_ts
        # "2026-01-11T00:00:00Z" -> "2026-01-11T00"
        bucket = from_ts[:13]

        self._index[bucket] = {
            "vault_path": vault_meta.get("vault_path"),
            "schema_version": vault_meta.get("schema_version"),
            "spine_sha256": vault_meta.get("spine_sha256"),
            "git_sha": vault_meta.get("git_sha"),
            "created_utc": vault_meta.get("created_utc"),
            "slice_bounds": bounds,
        }

    def lookup(self, timestamp: str) -> Optional[Dict[str, Any]]:
        """
        Look up envelope metadata for a timestamp.

        Args:
            timestamp: ISO8601 timestamp

        Returns:
            Envelope metadata if found, None otherwise
        """
        if not timestamp:
            return None

        bucket = timestamp[:13]
        return self._index.get(bucket)

    def __len__(self) -> int:
        return len(self._index)

    def buckets(self) -> list[str]:
        return sorted(self._index.keys())


def attach_envelope(
    intent_event: Dict[str, Any],
    vault_index: VaultIndex,
) -> Dict[str, Any]:
    """
    Attach envelope provenance to an intent event.

    If a vault envelope exists for the event's timestamp bucket,
    attaches provenance metadata. Otherwise, marks as mock envelope.

    Args:
        intent_event: Router intent or veto event
        vault_index: Index of available vault envelopes

    Returns:
        Event with envelope provenance attached
    """
    ts = intent_event.get("source_ts") or intent_event.get("timestamp", "")
    envelope_meta = vault_index.lookup(ts)

    payload = intent_event.get("payload", {})

    if envelope_meta:
        # Real vault envelope available
        payload["envelope_provenance"] = {
            "source": "vault",
            "vault_path": envelope_meta.get("vault_path"),
            "schema_version": envelope_meta.get("schema_version"),
            "spine_sha256": envelope_meta.get("spine_sha256"),
            "git_sha": envelope_meta.get("git_sha"),
        }
    else:
        # Fall back to mock envelope (already present in payload.envelope)
        payload["envelope_provenance"] = {
            "source": "mock",
            "reason": "no_vault_for_bucket",
        }

    intent_event["payload"] = payload
    return intent_event
