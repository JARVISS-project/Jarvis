from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Protocol


class _OpenWakeWordModel(Protocol):
    def predict(self, frame: Any) -> dict[str, float]: ...

    def reset(self) -> None: ...


class OpenWakeWordDetector:
    SAMPLE_RATE = 16000
    FRAME_LENGTH = 1280

    def __init__(
        self,
        model_name: str = "hey_jarvis",
        threshold: float = 0.5,
        minimum_consecutive_detections: int = 2,
        model_path: Path | str | None = None,
        model_factory: Callable[..., _OpenWakeWordModel] | None = None,
        pcm_converter: Callable[[Sequence[int]], Any] | None = None,
    ) -> None:
        if not 0.0 <= threshold <= 1.0:
            raise ValueError("threshold deve estar entre 0.0 e 1.0.")
        if minimum_consecutive_detections <= 0:
            raise ValueError("minimum_consecutive_detections deve ser positivo.")

        if model_factory is None:
            try:
                import numpy as np
                from openwakeword.model import Model
            except ImportError as error:
                raise RuntimeError(
                    "Suporte de voz não instalado. "
                    "Instale o extra 'voice'."
                ) from error
            model_factory = Model
            pcm_converter = lambda frame: np.asarray(frame, dtype=np.int16)

        model_identifier = (
            str(model_path) if model_path is not None else model_name
        )
        try:
            self._model: _OpenWakeWordModel | None = model_factory(
                wakeword_models=[model_identifier],
                inference_framework="onnx",
                device="cpu",
                ncpu=1,
            )
        except Exception as error:
            raise RuntimeError(
                "Não foi possível carregar o modelo de wake word. "
                "Execute 'python -m jarvis --setup-voice' uma vez "
                "ou configure um modelo ONNX local."
            ) from error

        self.model_name = model_name
        self.threshold = threshold
        self.minimum_consecutive_detections = minimum_consecutive_detections
        self._pcm_converter = pcm_converter or (lambda frame: frame)
        self.last_score = 0.0
        self.consecutive_detections = 0
        self.last_confirmation_frames = 0
        self._confirmed_during_active_run = False

    @property
    def frame_length(self) -> int:
        return self.FRAME_LENGTH

    @property
    def sample_rate(self) -> int:
        return self.SAMPLE_RATE

    def process(self, frame: Sequence[int]) -> bool:
        if self._model is None:
            raise RuntimeError("O detector de wake word já foi fechado.")

        scores = self._model.predict(self._pcm_converter(frame))
        self.last_score = max(scores.values()) if scores else 0.0
        if self.last_score < self.threshold:
            self.consecutive_detections = 0
            self._confirmed_during_active_run = False
            return False
        self.consecutive_detections += 1
        if self._confirmed_during_active_run:
            return False
        if self.consecutive_detections < self.minimum_consecutive_detections:
            return False
        self._confirmed_during_active_run = True
        self.last_confirmation_frames = self.consecutive_detections
        return True

    def reset(self) -> None:
        if self._model is not None:
            self._model.reset()
        self.last_score = 0.0
        self.consecutive_detections = 0
        self.last_confirmation_frames = 0
        self._confirmed_during_active_run = False

    def close(self) -> None:
        if self._model is not None:
            self.reset()
            self._model = None
