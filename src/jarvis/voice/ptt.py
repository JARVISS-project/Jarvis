"""Thread-safe PTT intent; only the voice worker owns audio and playback.

A release is latched until its capture finishes. Repeated presses cannot
replace an armed/released session. A press during ordinary capture adopts it.
"""

from enum import Enum, auto
from threading import RLock


class PttState(Enum):
    IDLE = auto()
    ARMED = auto()
    CAPTURING = auto()
    STOP_REQUESTED = auto()
    COMPLETED = auto()
    CANCELLED = auto()


class PttController:
    def __init__(self) -> None:
        self._lock = RLock()
        self._state = PttState.IDLE
        self._capturing = False
        self._closed = False

    @property
    def state(self) -> PttState:
        with self._lock:
            return self._state

    @property
    def explicit(self) -> bool:
        with self._lock:
            return self._state in {
                PttState.ARMED, PttState.CAPTURING, PttState.STOP_REQUESTED,
            }

    @property
    def pending(self) -> bool:
        with self._lock:
            return self.explicit and not self._capturing

    @property
    def stop_requested(self) -> bool:
        with self._lock:
            return self._closed or self._state is PttState.STOP_REQUESTED

    def press(self) -> bool:
        with self._lock:
            if self._closed or self.explicit:
                return False
            self._state = (
                PttState.CAPTURING if self._capturing else PttState.ARMED
            )
            return True

    def release(self) -> None:
        with self._lock:
            if self.explicit:
                self._state = PttState.STOP_REQUESTED

    def begin_capture(self) -> None:
        with self._lock:
            self._capturing = True
            if self._state is PttState.ARMED:
                self._state = PttState.CAPTURING

    def finish_capture(self) -> None:
        with self._lock:
            self._capturing = False
            if self.explicit:
                self._state = PttState.COMPLETED

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._state = PttState.CANCELLED
