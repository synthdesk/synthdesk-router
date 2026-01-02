from pathlib import Path
import json
import hashlib


def append_intent(path: Path, intent: dict, *, source_event_id: str, source_ts: str) -> None:
    if not isinstance(source_event_id, str):
        raise ValueError("source_event_id must be str")
    if not isinstance(source_ts, str):
        raise ValueError("source_ts must be str")
    digest = hashlib.sha256(
        (source_event_id + json.dumps(intent, sort_keys=True)).encode("utf-8")
    ).hexdigest()
    record = {
        "intent_id": digest,
        "timestamp": source_ts,
        "intent": intent,
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
