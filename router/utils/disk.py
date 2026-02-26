# synthdesk/utils/disk.py
"""
Disk safety utilities.

Purpose:
- provide a single, boring, correct way to check disk health
- provide ENOSPC-safe append semantics for non-critical writers
- provide a rate-limited write guard for hot loops

This module is INERT until explicitly wired by callers.
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path


def disk_ok(
    path: str | Path = "/",
    threshold: float = 0.95,
) -> bool:
    """
    Return True if disk usage at `path` is below `threshold`.

    - threshold is fraction used (e.g. 0.95 = 95%)
    - never raises
    - returns False on any error (fail-safe)
    """
    try:
        usage = shutil.disk_usage(str(path))
        used_frac = usage.used / usage.total
        return used_frac < threshold
    except Exception as e:
        _warn(f"disk_ok failed ({e}); treating as NOT OK")
        return False


class DiskGuard:
    """
    Rate-limited disk write guard for hot loops.

    Usage:
        _guard = DiskGuard()

        def write_event(...):
            if _guard.should_skip():
                return
            # ... actual write ...

    Behavior when disk >= threshold:
        - returns True (skip the write)
        - logs warning at most once per `warn_interval_s`
        - yields CPU every `yield_every` skips (cooperative SIGTERM)
    """

    __slots__ = (
        "_path", "_threshold", "_warn_interval",
        "_yield_every", "_last_warn_ts", "_skip_count",
    )

    def __init__(
        self,
        path: str | Path = "/",
        threshold: float = 0.95,
        warn_interval_s: float = 30.0,
        yield_every: int = 1000,
    ):
        self._path = path
        self._threshold = threshold
        self._warn_interval = warn_interval_s
        self._yield_every = yield_every
        self._last_warn_ts = 0.0
        self._skip_count = 0

    def should_skip(self) -> bool:
        """
        Return True if the caller should skip this write.

        - never raises
        - rate-limits warnings
        - yields to allow signal handling in tight loops
        """
        if disk_ok(self._path, self._threshold):
            # Disk is fine — reset skip counter
            if self._skip_count > 0:
                self._skip_count = 0
            return False

        self._skip_count += 1

        now = time.monotonic()
        if now - self._last_warn_ts >= self._warn_interval:
            _warn(
                f"disk guard active: skipping writes "
                f"(>={self._threshold:.0%} full, "
                f"{self._skip_count} skipped)"
            )
            self._last_warn_ts = now

        if self._skip_count % self._yield_every == 0:
            time.sleep(0.01)

        return True


def safe_append(
    path: str | Path,
    line: str,
    *,
    fsync: bool = False,
) -> bool:
    """
    Append a single line to `path`.

    - catches ENOSPC and all OSError
    - never raises
    - returns True if write succeeded, False otherwise
    - intended for non-critical diagnostic / soak writes only
    """
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)

        with p.open("a") as f:
            f.write(line)
            if not line.endswith("\n"):
                f.write("\n")
            if fsync:
                f.flush()
                os.fsync(f.fileno())

        return True

    except OSError as e:
        _warn(f"safe_append skipped ({e}) on {path}")
        return False

    except Exception as e:
        _warn(f"safe_append failed ({e}) on {path}")
        return False


# ---- internal helpers ----------------------------------------------------


def _warn(msg: str) -> None:
    """
    Internal warning. Intentionally minimal: stderr only, no logging framework.
    """
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        print(f"[disk] {ts} {msg}", file=sys.stderr, flush=True)
    except Exception:
        pass
