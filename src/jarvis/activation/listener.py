from collections.abc import Sequence
from datetime import datetime, timezone
import time
from typing import Callable, Protocol

from jarvis.activation.base import ActivationEvent, ActivationSource
from jarvis.audio.input import AudioInput


class WakeWordDetector(Protocol):
    def process(self, frame: Sequence[int]) -> bool: ...

    def reset(self) -> None: ...

    def close(self) -> None: ...


class WakeWordListener(ActivationSource):
    def __init__(
        self,
        audio_input: AudioInput,
        detector: WakeWordDetector,
        cooldown_seconds: float = 1.5,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if cooldown_seconds < 0:
            raise ValueError("cooldown_seconds não pode ser negativo.")
        self.audio_input = audio_input
        self.detector = detector
        self.cooldown_seconds = cooldown_seconds
        self._monotonic_clock = monotonic_clock
        self._last_activation_at: float | None = None
        self._started = False
        self._closed = False

    def start(self) -> None:
        if self._closed:
            raise RuntimeError("O listener de wake word já foi fechado.")
        if not self._started:
            self.audio_input.start()
            self._started = True

    def get_event(self) -> ActivationEvent | None:
        if not self._started:
            raise RuntimeError("O listener de wake word não foi iniciado.")

        frame = self.audio_input.read_frame()
        if not self.detector.process(frame):
            return None

        now = self._monotonic_clock()
        if (
            self._last_activation_at is not None
            and now - self._last_activation_at < self.cooldown_seconds
        ):
            return None
        self._last_activation_at = now

        return ActivationEvent(
            source="wake_word",
            timestamp=datetime.now(timezone.utc),
        )

    def stop(self) -> None:
        if self._started:
            self.audio_input.stop()
            self._started = False

    def reset(self) -> None:
        """Clear detector state and restart cooldown from this moment."""
        self.detector.reset()
        self._last_activation_at = self._monotonic_clock()

    def close(self) -> None:
        if self._closed:
            return
        self.stop()
        try:
            self.audio_input.close()
        finally:
            self.detector.close()
            self._closed = True
