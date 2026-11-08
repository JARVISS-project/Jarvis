from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

from jarvis.activation.corpus import capture_labeled_samples
from jarvis.activation.openwakeword import OpenWakeWordDetector
from jarvis.audio.input import PvRecorderAudioInput
from jarvis.audio.utterance import UtteranceRecorder
from jarvis.config import JarvisConfig


class FixedWindowRecorder:
    """Capture raw microphone PCM for a fixed window without speech gating."""

    def __init__(
        self,
        audio_input: PvRecorderAudioInput,
        *,
        sample_rate: int,
        duration_seconds: float,
        require_speech_presence: bool = False,
        speech_threshold: float = 500.0,
        minimum_speech_duration_seconds: float = 0.35,
        minimum_audio_energy: float = 700.0,
    ) -> None:
        if duration_seconds <= 0:
            raise ValueError("duration_seconds deve ser positivo.")
        if minimum_speech_duration_seconds <= 0:
            raise ValueError(
                "minimum_speech_duration_seconds deve ser positivo."
            )
        if speech_threshold < 0 or minimum_audio_energy < 0:
            raise ValueError("thresholds de fala não podem ser negativos.")
        self.audio_input = audio_input
        self.sample_rate = sample_rate
        self.duration_seconds = duration_seconds
        self.require_speech_presence = require_speech_presence
        self.speech_threshold = speech_threshold
        self.minimum_speech_duration_seconds = (
            minimum_speech_duration_seconds
        )
        self.minimum_audio_energy = minimum_audio_energy

    @staticmethod
    def _rms(samples: list[int]) -> float:
        if not samples:
            return 0.0
        return (
            sum(float(sample) * float(sample) for sample in samples)
            / len(samples)
        ) ** 0.5

    def _has_valid_speech(self, samples: list[int]) -> bool:
        frame_samples = max(1, int(self.sample_rate * 0.08))
        speech_samples = 0
        weighted_energy = 0.0
        for start in range(0, len(samples), frame_samples):
            frame = samples[start : start + frame_samples]
            energy = self._rms(frame)
            if energy < self.speech_threshold:
                continue
            speech_samples += len(frame)
            weighted_energy += energy * len(frame)

        minimum_speech_samples = int(
            self.minimum_speech_duration_seconds * self.sample_rate
        )
        average_speech_energy = (
            weighted_energy / speech_samples if speech_samples else 0.0
        )
        return (
            speech_samples >= minimum_speech_samples
            and average_speech_energy >= self.minimum_audio_energy
        )

    def record(self) -> list[int]:
        target_samples = int(self.sample_rate * self.duration_seconds)
        captured: list[int] = []
        while len(captured) < target_samples:
            frame = self.audio_input.read_frame()
            if not frame:
                raise RuntimeError("A entrada de áudio retornou um frame vazio.")
            remaining = target_samples - len(captured)
            captured.extend(frame[:remaining])
        if self.require_speech_presence and not self._has_valid_speech(captured):
            return []
        return captured


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Capture a local labeled wake-word corpus from the configured mic."
        )
    )
    parser.add_argument(
        "--label",
        choices=("positive", "negative"),
        required=True,
    )
    parser.add_argument("--count", type=int, default=5)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("data/voice-benchmark"),
    )
    parser.add_argument("--device-index", type=int, default=None)
    parser.add_argument(
        "--pre-speech-timeout",
        type=float,
        default=6.0,
    )
    parser.add_argument(
        "--max-recording-seconds",
        type=float,
        default=4.0,
    )
    parser.add_argument(
        "--fixed-window-seconds",
        type=float,
        default=None,
        help=(
            "Captura PCM bruto por uma janela fixa. Por padrão não aplica "
            "VAD/gate, preservando o áudio bruto recebido pelo runtime."
        ),
    )
    parser.add_argument(
        "--require-speech-presence",
        action="store_true",
        help=(
            "Com janela fixa, rejeita a tentativa se não houver duração e "
            "energia mínimas de fala. O WAV aceito continua contendo a janela "
            "bruta completa."
        ),
    )
    parser.add_argument(
        "--speech-presence-min-duration",
        type=float,
        default=None,
        help=(
            "Override da duração mínima de fala usada apenas pelo gate de "
            "presença em janela fixa."
        ),
    )
    parser.add_argument(
        "--speech-presence-min-energy",
        type=float,
        default=None,
        help=(
            "Override da energia mínima usada apenas pelo gate de presença "
            "em janela fixa."
        ),
    )
    parser.add_argument("--no-beep", action="store_true")
    parser.add_argument(
        "--prompt-text",
        default=None,
        help="Texto exato que o usuário deve dizer nesta captura.",
    )
    parser.add_argument(
        "--scenario",
        default=None,
        help=(
            "Rótulo curto da condição de captura, por exemplo "
            "near-normal, far-normal ou tv-noise."
        ),
    )
    parser.add_argument(
        "--arm-delay",
        type=float,
        default=1.0,
        help="Segundos entre o prompt e a abertura do microfone.",
    )
    parser.add_argument(
        "--wait-for-enter",
        action="store_true",
        help="Espera Enter antes de armar cada tentativa de captura.",
    )
    return parser


def _cue(
    index: int,
    total: int,
    *,
    label: str,
    prompt_text: str | None = None,
) -> None:
    phrase = (
        f'diga "{prompt_text}"'
        if prompt_text
        else (
            'diga "Hey Jarvis"'
            if label == "positive"
            else "fale sem usar a wake phrase"
        )
    )
    print(f"sample {index}/{total}: {phrase}", flush=True)


def _signal_ready(*, beep: bool) -> None:
    if not beep:
        return
    try:
        import winsound

        winsound.Beep(880, 120)
    except (ImportError, RuntimeError):
        print("\a", end="", flush=True)
    time.sleep(0.2)


def _arm_capture(
    audio_input: PvRecorderAudioInput,
    *,
    index: int,
    total: int,
    label: str,
    beep: bool,
    prompt_text: str | None,
    arm_delay: float,
    wait_for_enter: bool,
) -> None:
    audio_input.stop()
    _cue(
        index,
        total,
        label=label,
        prompt_text=prompt_text,
    )
    if wait_for_enter:
        input("press Enter to arm microphone...")
    if arm_delay > 0:
        print(f"arming microphone in {arm_delay:.1f}s...", flush=True)
        time.sleep(arm_delay)
    audio_input.start()
    _signal_ready(beep=beep)
    print("speak now", flush=True)


def _append_manifest(
    output_root: Path,
    *,
    label: str,
    prompt_text: str | None,
    scenario: str | None = None,
    paths: tuple[Path, ...],
) -> None:
    if not prompt_text and not scenario:
        return
    manifest = output_root / "capture_manifest.jsonl"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    with manifest.open("a", encoding="utf-8") as target:
        for path in paths:
            payload = {
                "path": str(path),
                "label": label,
            }
            if prompt_text:
                payload["expected_phrase"] = prompt_text
            if scenario:
                payload["scenario"] = scenario.strip()
            target.write(
                json.dumps(payload, ensure_ascii=False) + "\n"
            )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = JarvisConfig()
    device_index = (
        config.microphone_device_index
        if args.device_index is None
        else args.device_index
    )
    audio_input = PvRecorderAudioInput(
        frame_length=OpenWakeWordDetector.FRAME_LENGTH,
        device_index=device_index,
    )
    if args.fixed_window_seconds is not None:
        if args.fixed_window_seconds <= 0:
            raise SystemExit("--fixed-window-seconds deve ser positivo")
        recorder = FixedWindowRecorder(
            audio_input,
            sample_rate=OpenWakeWordDetector.SAMPLE_RATE,
            duration_seconds=args.fixed_window_seconds,
            require_speech_presence=args.require_speech_presence,
            speech_threshold=config.voice_silence_threshold,
            minimum_speech_duration_seconds=(
                config.voice_minimum_speech_duration_seconds
                if args.speech_presence_min_duration is None
                else args.speech_presence_min_duration
            ),
            minimum_audio_energy=(
                config.voice_minimum_audio_energy
                if args.speech_presence_min_energy is None
                else args.speech_presence_min_energy
            ),
        )
    else:
        recorder = UtteranceRecorder(
            audio_input=audio_input,
            sample_rate=OpenWakeWordDetector.SAMPLE_RATE,
            silence_threshold=config.voice_silence_threshold,
            silence_duration_seconds=config.voice_silence_duration_seconds,
            max_recording_seconds=args.max_recording_seconds,
            pre_speech_timeout_seconds=args.pre_speech_timeout,
            minimum_speech_duration_seconds=(
                config.voice_minimum_speech_duration_seconds
            ),
            minimum_audio_energy=config.voice_minimum_audio_energy,
            # Keep the cue outside the saved clip; capture uses the stable runtime
            # floor and leaves adaptive threshold tuning to the actual benchmark.
            noise_calibration_seconds=0.0,
            noise_margin=config.voice_noise_margin,
            maximum_speech_threshold=config.voice_maximum_speech_threshold,
            pre_roll_seconds=config.voice_pre_roll_seconds,
            end_silence_seconds=min(config.voice_end_silence_seconds, 0.8),
        )

    if args.arm_delay < 0:
        raise SystemExit("--arm-delay não pode ser negativo")

    try:
        result = capture_labeled_samples(
            recorder,
            output_root=args.output_root,
            label=args.label,
            count=args.count,
            sample_rate=OpenWakeWordDetector.SAMPLE_RATE,
            before_capture=lambda index, total: _arm_capture(
                audio_input,
                index=index,
                total=total,
                label=args.label,
                beep=not args.no_beep,
                prompt_text=args.prompt_text,
                arm_delay=args.arm_delay,
                wait_for_enter=args.wait_for_enter,
            ),
        )
    finally:
        audio_input.close()

    _append_manifest(
        args.output_root,
        label=args.label,
        prompt_text=args.prompt_text,
        scenario=args.scenario,
        paths=result.paths,
    )

    print(
        f"capture summary label={args.label} "
        f"captured={len(result.paths)}/{args.count} "
        f"attempts={result.attempts} "
        f"rejected={result.rejected_attempts}"
    )
    return 0 if len(result.paths) == args.count else 2


if __name__ == "__main__":
    raise SystemExit(main())
