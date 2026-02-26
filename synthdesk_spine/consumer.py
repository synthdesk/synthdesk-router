"""
SpineConsumer: Deterministic, resumable event spine reader.

Read-only, single-pass, offset-tracked consumption.
"""

import json
from pathlib import Path
from typing import Iterator, Optional
from datetime import datetime

from .envelope import EventEnvelope
from .errors import SpineCorruptionError, UnsupportedSchemaVersion


class SpineConsumer:
    """
    Deterministic event spine consumer.

    Features:
    - Single pass through spine file
    - Resumable via checkpoint file
    - Optional event type filtering
    - Optional time-based filtering
    - Offset tracking for determinism

    No mutation. No buffering. No queues.
    """

    def __init__(
        self,
        spine_path: Path,
        checkpoint_path: Optional[Path] = None,
    ):
        """
        Initialize spine consumer.

        Args:
            spine_path: Path to event_spine.jsonl file
            checkpoint_path: Optional path to store read offset
        """
        self.spine_path = spine_path
        self.checkpoint_path = checkpoint_path
        self._offset = self._load_checkpoint()

    def _load_checkpoint(self) -> int:
        """Load last read offset from checkpoint file."""
        if self.checkpoint_path and self.checkpoint_path.exists():
            try:
                return int(self.checkpoint_path.read_text().strip())
            except (ValueError, OSError):
                return 0
        return 0

    def _save_checkpoint(self, offset: int) -> None:
        """Save current offset to checkpoint file."""
        if self.checkpoint_path:
            try:
                self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
                self.checkpoint_path.write_text(str(offset))
            except OSError:
                pass  # Checkpoint save failure is non-fatal

    def poll(
        self,
        event_type: Optional[str] = None,
        since: Optional[datetime] = None,
    ) -> Iterator[EventEnvelope]:
        """
        Poll for new events since last checkpoint.

        Args:
            event_type: Optional filter for specific event type
            since: Optional filter for events after timestamp

        Yields:
            EventEnvelope instances matching filters

        Raises:
            SpineCorruptionError: If JSONL file is malformed
            UnsupportedSchemaVersion: If event schema version is unknown
            FileNotFoundError: If spine_path doesn't exist
        """
        if not self.spine_path.exists():
            raise FileNotFoundError(f"Spine file not found: {self.spine_path}")

        with self.spine_path.open("r") as f:
            for idx, line in enumerate(f):
                # Skip already-processed lines
                if idx < self._offset:
                    continue

                # Parse JSONL line
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError as e:
                    raise SpineCorruptionError(
                        idx, f"Invalid JSON: {e}"
                    ) from e

                # Reconstruct envelope
                try:
                    # Convert timestamp string to datetime if needed
                    if "timestamp" in raw and isinstance(raw["timestamp"], str):
                        raw["timestamp"] = datetime.fromisoformat(
                            raw["timestamp"].replace("Z", "+00:00")
                        )

                    event = EventEnvelope(**raw)
                    event.assert_schema_version()
                except UnsupportedSchemaVersion:
                    # Schema evolution: let this bubble up (not corruption)
                    raise
                except (TypeError, ValueError) as e:
                    raise SpineCorruptionError(
                        idx, f"Invalid envelope: {e}"
                    ) from e

                # Apply filters
                if event_type and event.event_type != event_type:
                    continue

                if since and event.timestamp <= since:
                    continue

                # Yield event and persist checkpoint when the generator advances or closes.
                try:
                    yield event
                finally:
                    self._offset = idx + 1
                    self._save_checkpoint(self._offset)

    def reset(self) -> None:
        """
        Reset consumer to beginning of spine.

        Clears checkpoint and offset.
        """
        self._offset = 0
        if self.checkpoint_path and self.checkpoint_path.exists():
            self.checkpoint_path.unlink()

    def seek_to_offset(self, offset: int) -> None:
        """
        Seek to specific line offset.

        Args:
            offset: Line number to start reading from (0-indexed)
        """
        if offset < 0:
            raise ValueError(f"offset must be >= 0, got {offset}")
        self._offset = offset
        self._save_checkpoint(offset)

    @property
    def current_offset(self) -> int:
        """Get current read offset."""
        return self._offset
