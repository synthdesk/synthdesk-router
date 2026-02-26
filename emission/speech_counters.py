"""
Speech emission counters.

Tracks whether each router decision cycle produced a speech event or a typed silence,
and periodically flushes aggregate counters for observability.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


# Canonical silence reasons used by speech_v1 + runtime guards.
INVALID_TIMESTAMP = "invalid_timestamp"
MISSING_ENVELOPE = "missing_envelope"
MISSING_HORIZON = "missing_horizon"
MISSING_PROBS = "missing_probs"
OTHER_INVARIANT_FAIL = "other_invariant_fail"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class SpeechCycle:
    _parent: "SpeechCounters"
    _finalized: bool = False
    _observed: bool = False

    def observe(self, speech_event: Optional[Dict[str, Any]], silence_reason: Optional[str]) -> None:
        if self._finalized:
            return

        self._observed = True
        self._parent.observations += 1
        self._parent.last_observed_ts = _utc_now_iso()

        if speech_event is not None:
            self._parent.speech_events += 1
            return

        reason = silence_reason or OTHER_INVARIANT_FAIL
        self._parent.silence_events += 1
        self._parent.silence_reasons[reason] = self._parent.silence_reasons.get(reason, 0) + 1

    def finalize(self) -> None:
        if self._finalized:
            return

        self._finalized = True
        self._parent.total_cycles += 1
        self._parent.last_cycle_ts = _utc_now_iso()

        if not self._observed:
            self._parent.no_observation_cycles += 1


class SpeechCounters:
    def __init__(self, runs_root: Path):
        self.runs_root = runs_root
        self.runs_root.mkdir(parents=True, exist_ok=True)
        self.output_path = self.runs_root / "speech_counters.json"

        self.total_cycles = 0
        self.observations = 0
        self.speech_events = 0
        self.silence_events = 0
        self.no_observation_cycles = 0
        self.silence_reasons: Dict[str, int] = {}
        self.last_cycle_ts: Optional[str] = None
        self.last_observed_ts: Optional[str] = None
        self.started_ts = _utc_now_iso()

    def tick(self) -> SpeechCycle:
        return SpeechCycle(self)

    def snapshot(self) -> Dict[str, Any]:
        return {
            "version": "1.0",
            "started_ts": self.started_ts,
            "updated_ts": _utc_now_iso(),
            "total_cycles": self.total_cycles,
            "observations": self.observations,
            "speech_events": self.speech_events,
            "silence_events": self.silence_events,
            "no_observation_cycles": self.no_observation_cycles,
            "silence_reasons": dict(sorted(self.silence_reasons.items())),
            "last_cycle_ts": self.last_cycle_ts,
            "last_observed_ts": self.last_observed_ts,
        }

    def flush(self) -> None:
        payload = self.snapshot()
        self.output_path.write_text(
            json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
