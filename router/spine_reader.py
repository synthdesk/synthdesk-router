"""
Spine reader - Event spine tail iterator.

Yields events from append-only JSONL spine.
"""

import json
import time
from pathlib import Path
from typing import Dict, Iterator, Optional


class SpineReader:
    """
    Tail-follow reader for event spine.

    Handles file rotation detection and graceful error handling.
    """

    def __init__(self, spine_path: Path, poll_interval: float = 1.0):
        """
        Initialize spine reader.

        Args:
            spine_path: Path to event_spine.jsonl
            poll_interval: Seconds between polls
        """
        self.spine_path = spine_path
        self.poll_interval = max(0.1, poll_interval)
        self.offset = 0
        self.inode: Optional[int] = None

    def _scan_existing(self) -> int:
        """
        Scan existing spine from start, update offset.

        Returns:
            Final offset after scanning
        """
        offset = 0
        try:
            with self.spine_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    offset = handle.tell()
        except OSError:
            return 0
        return offset

    def _refresh_offset(self) -> None:
        """
        Detect file rotation and rescan if needed.

        Updates self.offset and self.inode.
        """
        try:
            stat = self.spine_path.stat()
        except OSError:
            return

        # File rotation detected (inode changed or size decreased)
        if self.inode is None or stat.st_ino != self.inode or stat.st_size < self.offset:
            self.offset = self._scan_existing()
            self.inode = stat.st_ino

    def tail(self, skip_existing: bool = False) -> Iterator[Dict]:
        """
        Tail spine, yielding events as dicts.

        Args:
            skip_existing: If True, skip to end before yielding

        Yields:
            Event dicts parsed from JSONL
        """
        if skip_existing:
            self.offset = self._scan_existing()
            try:
                stat = self.spine_path.stat()
                self.inode = stat.st_ino
            except OSError:
                pass

        while True:
            if not self.spine_path.exists():
                time.sleep(self.poll_interval)
                continue

            self._refresh_offset()

            try:
                with self.spine_path.open("r", encoding="utf-8") as handle:
                    handle.seek(self.offset)
                    for line in handle:
                        self.offset = handle.tell()
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            event = json.loads(line)
                            if isinstance(event, dict):
                                yield event
                        except json.JSONDecodeError:
                            continue
            except OSError:
                pass

            time.sleep(self.poll_interval)

    def replay(self) -> Iterator[Dict]:
        """
        Replay spine from start (one-shot, no tailing).

        Yields:
            Event dicts parsed from JSONL
        """
        try:
            with self.spine_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                        if isinstance(event, dict):
                            yield event
                    except json.JSONDecodeError:
                        continue
        except OSError:
            return
