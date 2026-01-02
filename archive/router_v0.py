"""Minimal router v0 runtime for permission gating."""

raise RuntimeError("archived: non-runnable, non-authoritative")

from __future__ import annotations

import argparse
import json
import socket
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

DEFAULT_POLL_INTERVAL = 1.0


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_critical_violation(event: dict) -> bool:
    if event.get("event_type") != "invariant.violation":
        return False
    payload = event.get("payload")
    return isinstance(payload, dict) and payload.get("severity") == "critical"


def _emit_permission(
    event_spine: Path,
    permission: str,
    reason: str,
    since_event_id: str,
) -> None:
    record = {
        "event_id": str(uuid.uuid4()),
        "event_type": "router.permission",
        "timestamp": _utc_now_iso(),
        "source": "router",
        "version": "router/v0",
        "host": socket.gethostname(),
        "payload": {
            "permission": permission,
            "reason": reason,
            "since_event_id": since_event_id,
        },
    }
    line = json.dumps(record, separators=(",", ":"))
    with event_spine.open("a", encoding="utf-8", buffering=1) as handle:
        handle.write(line)
        handle.write("\n")
        handle.flush()
    print(f"router.permission {permission} reason={reason}")


def run(event_spine: Path, poll_interval: float) -> None:
    permission = "forbid"
    last_emitted_permission: Optional[str] = None
    seen_start = False
    seen_stop = False
    seen_crash = False
    seen_critical = False
    offset = 0

    poll = max(0.1, float(poll_interval))

    while True:
        if not event_spine.exists():
            time.sleep(poll)
            continue
        try:
            with event_spine.open("r", encoding="utf-8") as handle:
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
                    if not isinstance(event, dict):
                        continue
                    event_type = event.get("event_type")
                    if event_type == "router.permission":
                        continue
                    event_id = event.get("event_id")
                    if not isinstance(event_id, str):
                        continue

                    trigger_reason: Optional[str] = None
                    trigger_event_id: Optional[str] = None

                    if event_type == "listener.start":
                        seen_start = True
                        if not (seen_critical or seen_stop or seen_crash):
                            trigger_reason = "listener.start observed"
                            trigger_event_id = event_id
                    elif event_type == "listener.stop":
                        seen_stop = True
                        trigger_reason = "listener.stop observed"
                        trigger_event_id = event_id
                    elif event_type == "listener.crash":
                        seen_crash = True
                        trigger_reason = "listener.crash observed"
                        trigger_event_id = event_id
                    elif _is_critical_violation(event):
                        seen_critical = True
                        trigger_reason = "critical invariant.violation observed"
                        trigger_event_id = event_id

                    if not (seen_critical or seen_stop or seen_crash) and seen_start:
                        permission = "allow"
                    else:
                        permission = "forbid"

                    if trigger_reason and trigger_event_id:
                        if permission != last_emitted_permission:
                            try:
                                _emit_permission(
                                    event_spine,
                                    permission,
                                    trigger_reason,
                                    trigger_event_id,
                                )
                            except OSError:
                                continue
                            last_emitted_permission = permission
        except OSError:
            pass
        time.sleep(poll)


def cli() -> None:
    raise RuntimeError("router_v0 is retired")
    parser = argparse.ArgumentParser(description="Router v0 permission gate")
    parser.add_argument(
        "--event-spine",
        default="event_spine.jsonl",
        help="Path to the event_spine.jsonl file",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=DEFAULT_POLL_INTERVAL,
        help="Polling interval in seconds",
    )
    args = parser.parse_args()
    run(Path(args.event_spine), args.poll_interval)


if __name__ == "__main__":
    cli()
