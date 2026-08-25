from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Protocol


class AudioInput(ABC):
    @abstractmethod
    def start(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def read_frame(self) -> list[int]:
        raise NotImplementedError

    @abstractmethod
    def stop(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        raise NotImplementedError


class _Recorder(Protocol):
    def start(self) -> None: ...

    def read(self) -> list[int]: ...

    def stop(self) -> None: ...

    def delete(self) -> None: ...


class PvRecorderAudioInput(AudioInput):
    sample_rate = 16000
    sample_width_bits = 16

    def __init__(
        self,
        frame_length: int,
        device_index: int | None = None,
        recorder_factory: Callable[..., _Recorder] | None = None,
    ) -> None:
        if recorder_factory is None:
            try:
                from pvrecorder import PvRecorder
            except ImportError as error:
                raise RuntimeError(
                    "Suporte de voz não instalado. "
                    "Instale o extra 'voice'."
                ) from error
            recorder_factory = PvRecorder

        self._recorder = recorder_factory(
            device_index=-1 if device_index is None else device_index,
            frame_length=frame_length,
        )
        self._started = False
        self._closed = False

    def start(self) -> None:
        if self._closed:
            raise RuntimeError("A entrada de áudio já foi fechada.")
        if not self._started:
            self._recorder.start()
            self._started = True

    def read_frame(self) -> list[int]:
        if not self._started:
            raise RuntimeError("A entrada de áudio não foi iniciada.")
        return self._recorder.read()

    def stop(self) -> None:
        if self._started:
            self._recorder.stop()
            self._started = False

    def close(self) -> None:
        if self._closed:
            return
        try:
            self.stop()
        finally:
            self._recorder.delete()
            self._closed = True
