from __future__ import annotations

import argparse
import json
from pathlib import Path

from jarvis.activation.benchmark import (
    WakeBenchmarkResult,
    WakeBenchmarkSummary,
    WakeContinuousBenchmarkResult,
    WakePhaseBenchmarkSummary,
    benchmark_continuous_negative_stream,
    benchmark_wake_dataset,
    benchmark_wake_dataset_phases,
)
from jarvis.activation.openwakeword import OpenWakeWordDetector
from jarvis.config import JarvisConfig


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark the current local wake engine with labeled WAV clips."
        )
    )
    parser.add_argument(
        "--positive",
        action="append",
        type=Path,
        default=[],
        help="WAV file or directory that should activate Hey Jarvis.",
    )
    parser.add_argument(
        "--negative",
        action="append",
        type=Path,
        default=[],
        help="WAV file or directory that must not activate the wake word.",
    )
    parser.add_argument(
        "--manifest",
        action="append",
        type=Path,
        default=[],
        help=(
            "JSONL manifest from capture_wake_samples.py. Only entries "
            "explicitly labeled positive/negative are benchmarked."
        ),
    )
    parser.add_argument("--threshold", type=float, default=None)
    parser.add_argument(
        "--minimum-consecutive-detections",
        type=int,
        default=None,
    )
    parser.add_argument("--model", default=None)
    parser.add_argument("--model-path", type=Path, default=None)
    parser.add_argument("--verifier-path", type=Path, default=None)
    parser.add_argument(
        "--verifier-candidate-threshold",
        type=float,
        default=None,
    )
    parser.add_argument("--verifier-threshold", type=float, default=None)
    parser.add_argument(
        "--verifier-strong-threshold",
        type=float,
        default=None,
    )
    parser.add_argument(
        "--no-verifier",
        action="store_true",
        help="Ignora o verifier local mesmo que ele exista.",
    )
    parser.add_argument(
        "--phase-aware",
        action="store_true",
        help=(
            "Também testa cada WAV em múltiplas fases de início dentro "
            "do frame do detector."
        ),
    )
    parser.add_argument(
        "--phase-step-ms",
        type=float,
        default=10.0,
        help="Passo entre fases de início em milissegundos.",
    )
    parser.add_argument(
        "--phase-silence-seconds",
        type=float,
        default=2.0,
        help=(
            "Silêncio inicial por ensaio phase-aware; é alinhado ao "
            "frame antes de aplicar o deslocamento de fase."
        ),
    )
    parser.add_argument(
        "--phase-tail-silence-seconds",
        type=float,
        default=0.24,
        help=(
            "Pós-silêncio por ensaio phase-aware para permitir que o "
            "detector processe o fim da fala como faria no stream real."
        ),
    )
    parser.add_argument(
        "--continuous-negatives",
        action="store_true",
        help=(
            "Também reproduz todos os negativos como um único stream, "
            "sem reset/padding entre clips."
        ),
    )
    parser.add_argument(
        "--continuous-repeats",
        type=int,
        default=1,
        help="Número de ciclos do stream contínuo de negativos.",
    )
    parser.add_argument(
        "--continuous-silence-seconds",
        type=float,
        default=0.0,
        help="Silêncio inserido entre clips no stream contínuo.",
    )
    parser.add_argument(
        "--require-zero-false-accepts",
        action="store_true",
        help=(
            "Retorna exit code 1 se o benchmark normal ou phase-aware "
            "registrar qualquer false accept."
        ),
    )
    parser.add_argument(
        "--max-false-reject-rate",
        type=float,
        default=None,
        help=(
            "Retorna exit code 1 se a taxa de false reject normal ou "
            "phase-aware exceder este valor entre 0 e 1."
        ),
    )
    parser.add_argument(
        "--require-zero-continuous-activations",
        action="store_true",
        help=(
            "Retorna exit code 1 se o stream contínuo registrar ativação."
        ),
    )
    return parser


def _wav_files(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if path.is_file():
            if path.suffix.casefold() != ".wav":
                raise ValueError(f"{path}: arquivo não é WAV.")
            files.append(path)
            continue
        if path.is_dir():
            files.extend(
                candidate
                for candidate in path.rglob("*.wav")
                if candidate.is_file()
            )
            continue
        raise ValueError(f"{path}: caminho não encontrado.")
    return sorted(dict.fromkeys(files))



def _manifest_files(paths: list[Path]) -> tuple[list[Path], list[Path]]:
    positive: list[Path] = []
    negative: list[Path] = []
    for manifest in paths:
        if not manifest.is_file():
            raise ValueError(f"{manifest}: manifesto não encontrado.")
        with manifest.open("r", encoding="utf-8") as source:
            for line_number, raw_line in enumerate(source, start=1):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError as error:
                    raise ValueError(
                        f"{manifest}:{line_number}: JSON inválido."
                    ) from error
                label = str(payload.get("label", "")).strip().casefold()
                if label not in {"positive", "negative"}:
                    raise ValueError(
                        f"{manifest}:{line_number}: label inválido {label!r}."
                    )
                raw_path = payload.get("path")
                if not raw_path:
                    raise ValueError(
                        f"{manifest}:{line_number}: path ausente."
                    )
                clip = Path(str(raw_path))
                if not clip.is_file():
                    candidate = manifest.parent / clip
                    if candidate.is_file():
                        clip = candidate
                    else:
                        raise ValueError(
                            f"{manifest}:{line_number}: clip não encontrado: "
                            f"{raw_path}"
                        )
                if clip.suffix.casefold() != ".wav":
                    raise ValueError(
                        f"{manifest}:{line_number}: arquivo não é WAV: {clip}"
                    )
                (positive if label == "positive" else negative).append(clip)
    return (
        sorted(dict.fromkeys(positive)),
        sorted(dict.fromkeys(negative)),
    )

def _print_result(result: WakeBenchmarkResult) -> None:
    expected = "POS" if result.expected_activation else "NEG"
    verdict = (
        "PASS"
        if result.detected == result.expected_activation
        else "MISS" if result.expected_activation else "FALSE-ACCEPT"
    )
    activation = (
        "-"
        if result.activation_frame is None
        else str(result.activation_frame)
    )
    print(
        f"{expected} {verdict:<12} score={result.peak_score:.3f} "
        f"frame={activation:<4} duration={result.duration_seconds:.2f}s "
        f"{result.path}"
    )


def _acceptance_gate_failures(
    summary: WakeBenchmarkSummary,
    *,
    phase_summary: WakePhaseBenchmarkSummary | None = None,
    continuous_result: WakeContinuousBenchmarkResult | None = None,
    require_zero_false_accepts: bool = False,
    max_false_reject_rate: float | None = None,
    require_zero_continuous_activations: bool = False,
) -> list[str]:
    failures: list[str] = []
    summaries: list[
        tuple[str, WakeBenchmarkSummary | WakePhaseBenchmarkSummary]
    ] = [("base", summary)]
    if phase_summary is not None:
        summaries.append(("phase-aware", phase_summary))

    if require_zero_false_accepts:
        for label, candidate_summary in summaries:
            if candidate_summary.false_accepts:
                failures.append(
                    f"{label}: false_accepts={candidate_summary.false_accepts}"
                )

    if max_false_reject_rate is not None:
        for label, candidate_summary in summaries:
            if (
                candidate_summary.positives
                and candidate_summary.false_reject_rate
                > max_false_reject_rate
            ):
                failures.append(
                    f"{label}: false_reject_rate="
                    f"{candidate_summary.false_reject_rate:.3f} "
                    f"> {max_false_reject_rate:.3f}"
                )

    if (
        require_zero_continuous_activations
        and continuous_result is not None
        and continuous_result.activations
    ):
        failures.append(
            f"continuous: activations={continuous_result.activations}"
        )

    return failures


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.positive and not args.negative and not args.manifest:
        parser.error(
            "informe ao menos --positive, --negative ou --manifest"
        )

    try:
        positive = _wav_files(args.positive)
        negative = _wav_files(args.negative)
        manifest_positive, manifest_negative = _manifest_files(args.manifest)
        positive = sorted(dict.fromkeys(positive + manifest_positive))
        negative = sorted(dict.fromkeys(negative + manifest_negative))
    except ValueError as error:
        parser.error(str(error))
    if not positive and not negative:
        parser.error("nenhum arquivo WAV encontrado")
    if args.phase_aware:
        if args.phase_step_ms <= 0:
            parser.error("--phase-step-ms deve ser positivo")
        if args.phase_silence_seconds < 0:
            parser.error(
                "--phase-silence-seconds não pode ser negativo"
            )
        if args.phase_tail_silence_seconds < 0:
            parser.error(
                "--phase-tail-silence-seconds não pode ser negativo"
            )
    if args.continuous_negatives:
        if not negative:
            parser.error(
                "--continuous-negatives requer ao menos um negativo"
            )
        if args.continuous_repeats <= 0:
            parser.error("--continuous-repeats deve ser positivo")
        if args.continuous_silence_seconds < 0:
            parser.error(
                "--continuous-silence-seconds não pode ser negativo"
            )
    if (
        args.max_false_reject_rate is not None
        and not 0.0 <= args.max_false_reject_rate <= 1.0
    ):
        parser.error("--max-false-reject-rate deve estar entre 0 e 1")
    if (
        args.require_zero_continuous_activations
        and not args.continuous_negatives
    ):
        parser.error(
            "--require-zero-continuous-activations requer "
            "--continuous-negatives"
        )

    config = JarvisConfig()
    model_name = args.model or config.wake_word_model
    model_path = args.model_path or config.wake_word_model_path
    threshold = (
        config.wake_word_threshold
        if args.threshold is None
        else args.threshold
    )
    minimum_consecutive = (
        config.wake_min_consecutive_detections
        if args.minimum_consecutive_detections is None
        else args.minimum_consecutive_detections
    )
    verifier_path = None
    if not args.no_verifier:
        verifier_path = args.verifier_path
        if verifier_path is None and config.wake_word_verifier_path.is_file():
            verifier_path = config.wake_word_verifier_path
    verifier_candidate_threshold = (
        config.wake_word_verifier_candidate_threshold
        if args.verifier_candidate_threshold is None
        else args.verifier_candidate_threshold
    )
    verifier_threshold = (
        config.wake_word_verifier_threshold
        if args.verifier_threshold is None
        else args.verifier_threshold
    )
    verifier_strong_threshold = (
        config.wake_word_verifier_strong_threshold
        if args.verifier_strong_threshold is None
        else args.verifier_strong_threshold
    )
    detector = OpenWakeWordDetector(
        model_name=model_name,
        model_path=model_path,
        threshold=threshold,
        verifier_path=verifier_path,
        verifier_candidate_threshold=verifier_candidate_threshold,
        verifier_threshold=verifier_threshold,
        verifier_strong_threshold=verifier_strong_threshold,
        minimum_consecutive_detections=minimum_consecutive,
    )
    phase_summary = None
    continuous_result = None
    try:
        summary = benchmark_wake_dataset(
            detector,
            positive_paths=positive,
            negative_paths=negative,
        )
        if args.phase_aware:
            phase_summary = benchmark_wake_dataset_phases(
                detector,
                positive_paths=positive,
                negative_paths=negative,
                phase_step_ms=args.phase_step_ms,
                leading_silence_seconds=args.phase_silence_seconds,
                trailing_silence_seconds=args.phase_tail_silence_seconds,
            )
        if args.continuous_negatives:
            continuous_result = benchmark_continuous_negative_stream(
                detector,
                negative_paths=negative,
                repeats=args.continuous_repeats,
                silence_seconds=args.continuous_silence_seconds,
            )
    finally:
        detector.close()

    model_label = str(model_path or model_name)
    verifier_label = (
        "off"
        if verifier_path is None
        else (
            f"on candidate={verifier_candidate_threshold:.3f} "
            f"threshold={verifier_threshold:.3f} "
            f"strong={verifier_strong_threshold:.3f}"
        )
    )
    print(
        f"engine=openwakeword model={model_label} threshold={threshold:.3f} "
        f"minimum_consecutive={minimum_consecutive} "
        f"verifier={verifier_label}"
    )
    for result in summary.results:
        _print_result(result)

    print("\nSummary")
    print(
        f"positives={summary.positives} "
        f"true_positives={summary.true_positives} "
        f"false_rejects={summary.false_rejects} "
        f"false_reject_rate={summary.false_reject_rate:.1%}"
    )
    print(
        f"negatives={summary.negatives} "
        f"true_negatives={summary.true_negatives} "
        f"false_accepts={summary.false_accepts} "
        f"false_accept_rate={summary.false_accept_rate:.1%}"
    )
    if phase_summary is not None:
        print("\nPhase-aware summary")
        print(
            f"step={args.phase_step_ms:.1f}ms "
            f"silence={args.phase_silence_seconds:.2f}s "
            f"tail={args.phase_tail_silence_seconds:.2f}s "
            f"trials={len(phase_summary.results)}"
        )
        print(
            f"positives={phase_summary.positives} "
            f"true_positives={phase_summary.true_positives} "
            f"false_rejects={phase_summary.false_rejects} "
            f"false_reject_rate={phase_summary.false_reject_rate:.1%}"
        )
        print(
            f"negatives={phase_summary.negatives} "
            f"true_negatives={phase_summary.true_negatives} "
            f"false_accepts={phase_summary.false_accepts} "
            f"false_accept_rate={phase_summary.false_accept_rate:.1%}"
        )
        for result in phase_summary.results:
            if result.detected == result.expected_activation:
                continue
            expected = "POS" if result.expected_activation else "NEG"
            verdict = (
                "MISS"
                if result.expected_activation
                else "FALSE-ACCEPT"
            )
            print(
                f"PHASE {expected} {verdict:<12} "
                f"phase={result.onset_phase_ms:.1f}ms "
                f"score={result.peak_score:.3f} {result.path}"
            )

    if continuous_result is not None:
        print("\nContinuous negatives")
        print(
            f"clips={continuous_result.clip_count} "
            f"repeats={continuous_result.repeats} "
            f"silence={continuous_result.silence_seconds:.2f}s "
            f"duration={continuous_result.duration_seconds:.1f}s "
            f"peak_score={continuous_result.peak_score:.3f} "
            f"activations={continuous_result.activations}"
        )
        if continuous_result.activation_frames:
            print(
                "activation_frames="
                + ",".join(
                    str(frame)
                    for frame in continuous_result.activation_frames
                )
            )

    gate_failures = _acceptance_gate_failures(
        summary,
        phase_summary=phase_summary,
        continuous_result=continuous_result,
        require_zero_false_accepts=args.require_zero_false_accepts,
        max_false_reject_rate=args.max_false_reject_rate,
        require_zero_continuous_activations=(
            args.require_zero_continuous_activations
        ),
    )

    if gate_failures:
        print("\nGate FAIL")
        for failure in gate_failures:
            print(f"- {failure}")
        return 1
    if (
        args.require_zero_false_accepts
        or args.max_false_reject_rate is not None
        or args.require_zero_continuous_activations
    ):
        print("\nGate PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
