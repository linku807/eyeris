"""Reusable gaze-dwell activation state machine."""
from __future__ import annotations


class DwellSelector:
    """Fire once after a target is held for dwell_ms; leave/re-enter to re-arm."""
    def __init__(self, dwell_ms: int = 1000) -> None:
        self.dwell_ms = dwell_ms
        self.target: str | None = None
        self.started_ms = 0
        self.fired = False
        self.progress = 0.0

    def reset(self) -> None:
        self.target = None
        self.started_ms = 0
        self.fired = False
        self.progress = 0.0

    def update(self, target: str | None, now_ms: int) -> bool:
        if target != self.target:
            self.target = target
            self.started_ms = now_ms
            self.fired = False
            self.progress = 0.0
        if target is None:
            return False
        elapsed = max(0, now_ms - self.started_ms)
        self.progress = min(1.0, elapsed / max(1, self.dwell_ms))
        if self.progress >= 1.0 and not self.fired:
            self.fired = True
            return True
        return False
