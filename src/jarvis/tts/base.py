from abc import ABC, abstractmethod
from threading import Event


class SpeechPlaybackHandle:
    def __init__(
        self,
        finished_event: Event,
        stopper: object,
        error_getter: object | None = None,
        *,
        started_event: Event | None = None,
        interrupted_getter: object | None = None,
    ) -> None:
        self._finished_event = finished_event
        self._started_event = started_event or Event()
        if started_event is None:
            self._started_event.set()
        self._stopper = stopper
        self._error_getter = error_getter
        self._interrupted_getter = interrupted_getter

    @property
    def started_event(self) -> Event:
        return self._started_event

    @property
    def finished_event(self) -> Event:
        return self._finished_event

    @property
    def started(self) -> bool:
        return self._started_event.is_set()

    @property
    def finished(self) -> bool:
        return self._finished_event.is_set()

    @property
    def interrupted(self) -> bool:
        return bool(
            self._interrupted_getter()
            if callable(self._interrupted_getter)
            else False
        )

    @property
    def error(self) -> Exception | None:
        if callable(self._error_getter):
            return self._error_getter()
        return None

    @property
    def is_playing(self) -> bool:
        return not self._finished_event.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        finished = self._finished_event.wait(timeout)
        if finished and self.error is not None:
            raise self.error
        return finished

    def stop(self) -> None:
        if callable(self._stopper):
            self._stopper()


class TextToSpeech(ABC):
    # Short acknowledgement utterances are useful with low-latency TTS, but
    # expensive neural backends should skip them to avoid a second generation
    # before every real answer.
    fast_acknowledgement: bool = True

    @abstractmethod
    def speak(self, text: str) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        pass

    def speak_async(self, text: str) -> SpeechPlaybackHandle:
        started = Event()
        finished = Event()
        started.set()
        self.speak(text)
        finished.set()
        return SpeechPlaybackHandle(
            finished, self.stop, started_event=started
        )

    def close(self) -> None:
        self.stop()
