"""Diagnóstico local de ASR; nunca participa de autorização ou execução."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from threading import Lock
from typing import Any

from jarvis.speech.quality import (
    TranscriptionProfile,
    TranscriptionValidation,
)


@dataclass(frozen=True, slots=True)
class SpeechRecognitionMetric:
    schema_version: int
    timestamp_utc: str
    profile: str
    quality: str
    reason: str
    retry_used: bool
    stability: float | None
    captured_duration_ms: int
    speech_duration_ms: int | None
    stt_latency_ms: int
    average_log_probability: float | None
    no_speech_probability: float | None
    language: str | None
    language_probability: float | None
    text_characters: int
    text_words: int


@dataclass(frozen=True, slots=True)
class SpeechRecognitionMetricsSummary:
    total: int
    accepted: int
    borderline: int
    rejected: int
    retries: int
    malformed_lines: int
    reasons: dict[str, int]


class SpeechRecognitionMetricsRecorder:
    """Persiste métricas locais sem áudio nem conteúdo transcrito."""

    def __init__(
        self,
        path: Path,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.path = Path(path)
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._lock = Lock()

    def record(
        self,
        *,
        result: object,
        validation: TranscriptionValidation,
        profile: TranscriptionProfile,
        captured_samples: int,
        sample_rate: int,
        elapsed_seconds: float,
        retry_used: bool,
    ) -> bool:
        if sample_rate <= 0 or captured_samples < 0:
            return False
        text = str(getattr(result, "text", "") or "")
        speech_seconds = _finite_float(
            getattr(result, "duration_after_vad_seconds", None)
        )
        metric = SpeechRecognitionMetric(
            schema_version=1,
            timestamp_utc=self._timestamp(),
            profile=profile.value,
            quality=validation.quality.value,
            reason=validation.reason,
            retry_used=bool(retry_used),
            stability=_finite_float(validation.stability),
            captured_duration_ms=_seconds_to_ms(
                captured_samples / sample_rate
            ),
            speech_duration_ms=(
                _seconds_to_ms(speech_seconds)
                if speech_seconds is not None
                else None
            ),
            stt_latency_ms=_seconds_to_ms(elapsed_seconds),
            average_log_probability=_finite_float(
                getattr(result, "average_log_probability", None)
            ),
            no_speech_probability=_finite_float(
                getattr(result, "no_speech_probability", None)
            ),
            language=_clean_language(getattr(result, "language", None)),
            language_probability=_finite_float(
                getattr(result, "language_probability", None)
            ),
            text_characters=len(text),
            text_words=len(text.split()),
        )
        try:
            payload = json.dumps(
                asdict(metric),
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            )
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as target:
                    target.write(payload + "\n")
        except (OSError, UnicodeError, TypeError, ValueError):
            return False
        return True

    def _timestamp(self) -> str:
        value = self._now()
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()


def summarize_speech_metrics(path: Path) -> SpeechRecognitionMetricsSummary:
    path = Path(path)
    counts = {"accepted": 0, "borderline": 0, "rejected": 0}
    reasons: dict[str, int] = {}
    retries = 0
    malformed = 0
    total = 0
    if not path.exists():
        return SpeechRecognitionMetricsSummary(
            0, 0, 0, 0, 0, 0, {}
        )
    try:
        with path.open("r", encoding="utf-8") as source:
            for line in source:
                if not line.strip():
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    malformed += 1
                    continue
                if not isinstance(payload, dict):
                    malformed += 1
                    continue
                quality = payload.get("quality")
                reason = payload.get("reason")
                if quality not in counts or not isinstance(reason, str):
                    malformed += 1
                    continue
                total += 1
                counts[quality] += 1
                reasons[reason] = reasons.get(reason, 0) + 1
                if payload.get("retry_used") is True:
                    retries += 1
    except (OSError, UnicodeError):
        return SpeechRecognitionMetricsSummary(
            0, 0, 0, 0, 0, 1, {}
        )
    return SpeechRecognitionMetricsSummary(
        total=total,
        accepted=counts["accepted"],
        borderline=counts["borderline"],
        rejected=counts["rejected"],
        retries=retries,
        malformed_lines=malformed,
        reasons=dict(sorted(reasons.items())),
    )


def _seconds_to_ms(value: float) -> int:
    finite = _finite_float(value)
    if finite is None or finite <= 0:
        return 0
    return max(0, int(round(finite * 1000.0)))


def _finite_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    converted = float(value)
    return converted if math.isfinite(converted) else None


def _clean_language(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None
