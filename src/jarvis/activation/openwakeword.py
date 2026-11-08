from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from jarvis.activation.verifier import SafeWakeVerifier


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
        verifier_path: Path | str | None = None,
        verifier_candidate_threshold: float = 0.12,
        verifier_threshold: float = 0.74,
        verifier_strong_threshold: float = 0.85,
        model_factory: Callable[..., _OpenWakeWordModel] | None = None,
        pcm_converter: Callable[[Sequence[int]], Any] | None = None,
        verifier_loader: Callable[
            [Path | str], SafeWakeVerifier
        ] | None = None,
    ) -> None:
        if not 0.0 <= threshold <= 1.0:
            raise ValueError("threshold deve estar entre 0.0 e 1.0.")
        if minimum_consecutive_detections <= 0:
            raise ValueError(
                "minimum_consecutive_detections deve ser positivo."
            )
        if not 0.0 <= verifier_candidate_threshold <= 1.0:
            raise ValueError(
                "verifier_candidate_threshold deve estar entre 0.0 e 1.0."
            )
        if not 0.0 <= verifier_threshold <= 1.0:
            raise ValueError(
                "verifier_threshold deve estar entre 0.0 e 1.0."
            )
        if not verifier_threshold <= verifier_strong_threshold <= 1.0:
            raise ValueError(
                "verifier_strong_threshold deve estar entre "
                "verifier_threshold e 1.0."
            )

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
        self.verifier_candidate_threshold = verifier_candidate_threshold
        self.verifier_threshold = verifier_threshold
        self.verifier_strong_threshold = verifier_strong_threshold
        self._pcm_converter = pcm_converter or (lambda frame: frame)
        self.verifier_load_error: str | None = None
        if verifier_path is None:
            self._verifier = None
        else:
            if verifier_loader is None:
                from jarvis.activation.verifier import (
                    load_safe_wake_verifier,
                )

                verifier_loader = load_safe_wake_verifier
            try:
                self._verifier = verifier_loader(verifier_path)
            except (FileNotFoundError, OSError, ValueError) as error:
                # O verifier é uma adaptação opcional. Se o artefato local
                # estiver ausente/corrompido, voltar ao baseline conservador
                # é mais seguro e mais disponível do que perder o Voice.
                self._verifier = None
                self.verifier_load_error = str(error)

        self.last_base_score = 0.0
        self.last_verifier_score: float | None = None
        self.last_verifier_error: str | None = None
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

    @property
    def verifier_enabled(self) -> bool:
        return self._verifier is not None

    @property
    def activation_threshold(self) -> float:
        return (
            self.verifier_threshold
            if self._verifier is not None
            else self.threshold
        )

    def _score_with_verifier(self) -> float:
        model = self._model
        verifier = self._verifier
        if model is None or verifier is None:
            raise RuntimeError("Wake verifier indisponível.")

        preprocessor = getattr(model, "preprocessor", None)
        model_inputs = getattr(model, "model_inputs", None)
        get_features = getattr(preprocessor, "get_features", None)
        if not callable(get_features) or not isinstance(model_inputs, dict):
            raise ValueError(
                "Modelo de wake word não expõe features para o verifier."
            )
        if verifier.model_name not in model_inputs:
            raise ValueError(
                "Wake verifier não corresponde ao modelo de wake word ativo."
            )

        features = get_features(model_inputs[verifier.model_name])
        return verifier.score(features)

    def process(self, frame: Sequence[int]) -> bool:
        if self._model is None:
            raise RuntimeError("O detector de wake word já foi fechado.")

        scores = self._model.predict(self._pcm_converter(frame))
        self.last_base_score = (
            float(max(scores.values())) if scores else 0.0
        )
        self.last_verifier_score = None
        self.last_verifier_error = None

        effective_score = self.last_base_score
        if self._verifier is not None:
            effective_score = 0.0
            if self.last_base_score >= self.verifier_candidate_threshold:
                try:
                    effective_score = self._score_with_verifier()
                except (AttributeError, KeyError, TypeError, ValueError) as error:
                    self.last_verifier_error = str(error)
                    effective_score = 0.0
                else:
                    self.last_verifier_score = effective_score

        self.last_score = effective_score
        if self.last_score < self.activation_threshold:
            self.consecutive_detections = 0
            self._confirmed_during_active_run = False
            return False

        if self._confirmed_during_active_run:
            return False

        if (
            self._verifier is not None
            and self.last_score >= self.verifier_strong_threshold
        ):
            self.consecutive_detections = 1
            self._confirmed_during_active_run = True
            self.last_confirmation_frames = 1
            return True

        self.consecutive_detections += 1
        if self.consecutive_detections < self.minimum_consecutive_detections:
            return False
        self._confirmed_during_active_run = True
        self.last_confirmation_frames = self.consecutive_detections
        return True

    def reset(self) -> None:
        if self._model is not None:
            self._model.reset()
        self.last_base_score = 0.0
        self.last_verifier_score = None
        self.last_verifier_error = None
        self.last_score = 0.0
        self.consecutive_detections = 0
        self.last_confirmation_frames = 0
        self._confirmed_during_active_run = False

    def close(self) -> None:
        if self._model is not None:
            self.reset()
            self._model = None
