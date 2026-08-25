from abc import ABC, abstractmethod
from threading import Event


class SpeechPlaybackHandle:
    def __init__(
        self,
        completed: Event,
        stopper: object,
        error_getter: object | None = None,
    ) -> None:
        self._completed = completed
        self._stopper = stopper
        self._error_getter = error_getter

    @property
    def is_playing(self) -> bool:
        return not self._completed.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        completed = self._completed.wait(timeout)
        if completed and callable(self._error_getter):
            error = self._error_getter()
            if error is not None:
                raise error
        return completed

    def stop(self) -> None:
        if callable(self._stopper):
            self._stopper()


class TextToSpeech(ABC):
    @abstractmethod
    def speak(self, text: str) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        pass

    def speak_async(self, text: str) -> SpeechPlaybackHandle:
        completed = Event()
        self.speak(text)
        completed.set()
        return SpeechPlaybackHandle(completed, self.stop)

    def close(self) -> None:
        self.stop()
