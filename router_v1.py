"""Minimal router v1 runtime (read-only)."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from synthdesk_spine import InvariantRegistry

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


def _log_invariant_classification(registry: InvariantRegistry, event: dict) -> None:
    payload = event.get("payload")
    invariant_id = payload.get("invariant_id") if isinstance(payload, dict) else None
    if not isinstance(invariant_id, str):
        invariant_id = "unknown"
    inv = registry.get_invariant(invariant_id) if invariant_id != "unknown" else None
    if inv is None:
        severity = "unknown"
        action = "continue"
        applies_to = "unknown"
        description = "unregistered invariant"
    else:
        severity = inv.severity
        action = inv.action
        applies_to = inv.applies_to
        description = inv.description
    event_id = event.get("event_id")
    if not isinstance(event_id, str):
        event_id = "unknown"
    log = {
        "kind": "invariant.classification",
        "event_id": event_id,
        "invariant_id": invariant_id,
        "severity": severity,
        "action": action,
        "applies_to": applies_to,
        "description": description,
    }
    print(json.dumps(log, sort_keys=True), flush=True)


def run() -> None:
    blocked = False
    emitted = False
    offset = 0
    inode: int | None = None
    poll = max(0.1, POLL_INTERVAL_SECONDS)
    registry = InvariantRegistry()

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
                        _log_invariant_classification(registry, event)
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
