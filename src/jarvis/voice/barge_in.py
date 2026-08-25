"""Detecção leve de intervenção do usuário durante playback."""

from collections import deque
import math

from jarvis.audio.input import AudioInput
from jarvis.tts.base import SpeechPlaybackHandle


class BargeInController:
    def __init__(
        self,
        audio_input: AudioInput,
        sample_rate: int = 16000,
        energy_margin: float = 1200.0,
        minimum_speech_duration_seconds: float = 0.25,
        grace_period_seconds: float = 0.35,
        silence_duration_seconds: float = 0.6,
        maximum_capture_seconds: float = 8.0,
        pre_roll_seconds: float = 0.2,
        minimum_threshold: float = 900.0,
    ) -> None:
        self.audio_input = audio_input
        self.sample_rate = sample_rate
        self.energy_margin = energy_margin
        self.minimum_speech_duration_seconds = minimum_speech_duration_seconds
        self.grace_period_seconds = grace_period_seconds
        self.silence_duration_seconds = silence_duration_seconds
        self.maximum_capture_seconds = maximum_capture_seconds
        self.pre_roll_seconds = pre_roll_seconds
        self.minimum_threshold = minimum_threshold

    def listen(self, playback: SpeechPlaybackHandle) -> list[int] | None:
        grace_samples = int(self.grace_period_seconds * self.sample_rate)
        required_speech = int(
            self.minimum_speech_duration_seconds * self.sample_rate
        )
        required_silence = int(self.silence_duration_seconds * self.sample_rate)
        maximum_capture = int(self.maximum_capture_seconds * self.sample_rate)
        pre_roll: deque[int] = deque(
            maxlen=int(self.pre_roll_seconds * self.sample_rate)
        )
        observed = 0
        weighted_energy = 0.0
        consecutive_speech = 0
        candidate: list[int] = []

        while playback.is_playing:
            frame = self.audio_input.read_frame()
            if not frame:
                return None
            energy = self._rms(frame)
            if observed < grace_samples:
                usable = min(len(frame), grace_samples - observed)
                weighted_energy += energy * usable
                observed += usable
                pre_roll.extend(frame)
                continue
            noise_floor = weighted_energy / observed if observed else 0.0
            threshold = max(self.minimum_threshold, noise_floor + self.energy_margin)
            if energy >= threshold:
                consecutive_speech += len(frame)
                candidate.extend(frame)
                if consecutive_speech >= required_speech:
                    playback.stop()
                    captured = list(pre_roll) + candidate
                    return self._finish_capture(
                        captured, threshold, required_silence, maximum_capture
                    )
            else:
                consecutive_speech = 0
                candidate.clear()
                pre_roll.extend(frame)
        return None

    def _finish_capture(
        self,
        captured: list[int],
        threshold: float,
        required_silence: int,
        maximum_capture: int,
    ) -> list[int]:
        silence = 0
        while len(captured) < maximum_capture:
            frame = self.audio_input.read_frame()
            if not frame:
                break
            captured.extend(frame[: maximum_capture - len(captured)])
            if self._rms(frame) >= threshold:
                silence = 0
            else:
                silence += len(frame)
                if silence >= required_silence:
                    break
        return captured

    @staticmethod
    def _rms(frame: list[int]) -> float:
        return math.sqrt(sum(sample * sample for sample in frame) / len(frame))
