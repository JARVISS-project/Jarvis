import math

from jarvis.audio.input import AudioInput


class UtteranceRecorder:
    def __init__(
        self,
        audio_input: AudioInput,
        sample_rate: int = 16000,
        silence_threshold: float = 500.0,
        silence_duration_seconds: float = 0.8,
        max_recording_seconds: float = 15.0,
        pre_speech_timeout_seconds: float = 5.0,
        minimum_speech_duration_seconds: float = 0.35,
        minimum_audio_energy: float = 700.0,
    ) -> None:
        if sample_rate <= 0:
            raise ValueError("sample_rate deve ser positivo.")
        if silence_threshold < 0:
            raise ValueError("silence_threshold não pode ser negativo.")
        if silence_duration_seconds <= 0:
            raise ValueError("silence_duration_seconds deve ser positivo.")
        if max_recording_seconds <= 0:
            raise ValueError("max_recording_seconds deve ser positivo.")
        if pre_speech_timeout_seconds <= 0:
            raise ValueError("pre_speech_timeout_seconds deve ser positivo.")
        if minimum_speech_duration_seconds <= 0:
            raise ValueError(
                "minimum_speech_duration_seconds deve ser positivo."
            )
        if minimum_audio_energy < 0:
            raise ValueError("minimum_audio_energy não pode ser negativo.")

        self.audio_input = audio_input
        self.sample_rate = sample_rate
        self.silence_threshold = silence_threshold
        self.silence_duration_seconds = silence_duration_seconds
        self.max_recording_seconds = max_recording_seconds
        self.pre_speech_timeout_seconds = pre_speech_timeout_seconds
        self.minimum_speech_duration_seconds = (
            minimum_speech_duration_seconds
        )
        self.minimum_audio_energy = minimum_audio_energy

    def record(self) -> list[int]:
        captured: list[int] = []
        speech_started = False
        elapsed_samples = 0
        silence_samples = 0
        speech_samples = 0
        weighted_speech_energy = 0.0
        max_samples = int(self.max_recording_seconds * self.sample_rate)
        pre_speech_samples = int(
            self.pre_speech_timeout_seconds * self.sample_rate
        )
        required_silence_samples = int(
            self.silence_duration_seconds * self.sample_rate
        )

        while elapsed_samples < max_samples:
            frame = self.audio_input.read_frame()
            if not frame:
                raise RuntimeError("A entrada de áudio retornou um frame vazio.")

            remaining_samples = max_samples - elapsed_samples
            current_frame = frame[:remaining_samples]
            elapsed_samples += len(current_frame)
            frame_energy = self._rms(current_frame)
            has_speech = frame_energy >= self.silence_threshold

            if not speech_started:
                if has_speech:
                    speech_started = True
                    captured.extend(current_frame)
                    speech_samples += len(current_frame)
                    weighted_speech_energy += (
                        frame_energy * len(current_frame)
                    )
                elif elapsed_samples >= pre_speech_samples:
                    return []
                continue

            captured.extend(current_frame)
            if has_speech:
                silence_samples = 0
                speech_samples += len(current_frame)
                weighted_speech_energy += frame_energy * len(current_frame)
            else:
                silence_samples += len(current_frame)
                if silence_samples >= required_silence_samples:
                    break

        minimum_speech_samples = int(
            self.minimum_speech_duration_seconds * self.sample_rate
        )
        average_speech_energy = (
            weighted_speech_energy / speech_samples
            if speech_samples
            else 0.0
        )
        if (
            speech_samples < minimum_speech_samples
            or average_speech_energy < self.minimum_audio_energy
        ):
            return []

        return captured

    @staticmethod
    def _rms(frame: list[int]) -> float:
        mean_square = sum(sample * sample for sample in frame) / len(frame)
        return math.sqrt(mean_square)
