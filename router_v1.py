"""Minimal router v1 runtime (read-only)."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

EVENT_SPINE = Path("/root/synthdesk-listener/runs/0.2.0/event_spine.jsonl")
POLL_INTERVAL_SECONDS = 1.0

sys.dont_write_bytecode = True


def _scan_existing(path: Path) -> tuple[bool, int]:
    blocked = False
    offset = 0
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                offset = handle.tell()
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict) and event.get("event_type") == "invariant.violation":
                    blocked = True
    except OSError:
        return False, 0
    return blocked, offset


def _refresh_offset(path: Path, inode: int | None, offset: int, blocked: bool) -> tuple[int | None, int, bool]:
    try:
        stat = path.stat()
    except OSError:
        return inode, offset, blocked
    if inode is None or stat.st_ino != inode or stat.st_size < offset:
        scanned_blocked, offset = _scan_existing(path)
        blocked = blocked or scanned_blocked
        inode = stat.st_ino
    return inode, offset, blocked


def run() -> None:
    blocked = False
    emitted = False
    offset = 0
    inode: int | None = None
    poll = max(0.1, POLL_INTERVAL_SECONDS)

    while True:
        if not EVENT_SPINE.exists():
            time.sleep(poll)
            continue
        inode, offset, blocked = _refresh_offset(EVENT_SPINE, inode, offset, blocked)
        try:
            with EVENT_SPINE.open("r", encoding="utf-8") as handle:
                handle.seek(offset)
                for line in handle:
                    offset = handle.tell()
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(event, dict) and event.get("event_type") == "invariant.violation":
                        blocked = True
                    if not blocked and not emitted:
                        print("router.permission: allow", flush=True)
                        emitted = True
        except OSError:
            pass
        time.sleep(poll)


def cli() -> None:
    run()


if __name__ == "__main__":
    cli()
