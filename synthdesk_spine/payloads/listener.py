"""
Listener domain event payloads.
"""

from dataclasses import dataclass
from typing import Literal, Optional, Tuple


@dataclass(frozen=True)
class ListenerStartPayload:
    """
    Payload for listener.start event.

    Emitted when listener begins operation.
    """

    pid: int
    poll_interval_seconds: float
    pairs: Tuple[str, ...]
    uptime_prev: Optional[float]  # Previous session uptime in seconds, if resumed

    def __post_init__(self) -> None:
        if isinstance(self.pairs, list):
            object.__setattr__(self, "pairs", tuple(self.pairs))
        elif not isinstance(self.pairs, tuple):
            raise TypeError("pairs must be a tuple or list of strings")


@dataclass(frozen=True)
class ListenerStopPayload:
    """
    Payload for listener.stop event.

    Emitted on graceful shutdown.
    """

    reason: Literal["keyboard_interrupt", "signal_term", "requested"]
    uptime_seconds: float


@dataclass(frozen=True)
class ListenerCrashPayload:
    """
    Payload for listener.crash event.

    Emitted on unexpected termination.
    """

    exception_type: str
    exception_message: str
    traceback: str
    uptime_seconds: float
