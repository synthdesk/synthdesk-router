"""
EventEnvelope: Constitutional object for event spine.

This is the hard freeze. All events on the spine share this structure.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict

from .errors import UnsupportedSchemaVersion

EVENT_ENVELOPE_VERSION = "1.0"


@dataclass(frozen=True)
class EventEnvelope:
    """
    Canonical event envelope.

    Fixed fields, strict validation, immutable.

    Fields:
        event_id: Opaque string identifier. May be UUIDv4 string (random events)
                  or deterministic hash (regime events post-epoch). Both valid.
        event_type: Namespaced event type (see event_types.py)
        timestamp: UTC datetime (timezone-aware, ISO-8601)
        source: Emitting package (e.g., "synthdesk_listener")
        version: Emitter version (e.g., "synthdesk-listener@0.2.0")
        schema_version: Event envelope schema version (this structure)
        host: Hostname of emitting machine
        payload: Event-specific data (dict)
    """

    event_id: str
    event_type: str
    timestamp: datetime
    source: str
    version: str
    schema_version: str
    host: str
    payload: Dict[str, Any]

    def assert_schema_version(self) -> None:
        """
        Validate schema version matches expected.

        Raises:
            UnsupportedSchemaVersion: If schema_version is unknown
        """
        if self.schema_version != EVENT_ENVELOPE_VERSION:
            raise UnsupportedSchemaVersion(self.schema_version)

    def __post_init__(self) -> None:
        """Validate envelope structure on construction."""
        if not isinstance(self.event_id, str):
            raise TypeError("event_id must be str")
        if not isinstance(self.event_type, str):
            raise TypeError("event_type must be str")
        if not isinstance(self.timestamp, datetime):
            raise TypeError("timestamp must be datetime")
        if self.timestamp.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware (UTC)")
        if not isinstance(self.source, str):
            raise TypeError("source must be str")
        if not isinstance(self.version, str):
            raise TypeError("version must be str")
        if not isinstance(self.schema_version, str):
            raise TypeError("schema_version must be str")
        if not isinstance(self.host, str):
            raise TypeError("host must be str")
        if not isinstance(self.payload, dict):
            raise TypeError("payload must be dict")
