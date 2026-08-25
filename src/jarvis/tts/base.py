from abc import ABC, abstractmethod


class TextToSpeech(ABC):
    @abstractmethod
    def speak(self, text: str) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        pass

    def close(self) -> None:
        self.stop()
