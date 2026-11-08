"""Envelope de atividade do áudio reproduzido pelo TTS.

A UI recebe apenas escalares normalizados. O PCM continua restrito ao backend
de TTS/player e nunca atravessa o barramento de observabilidade.
"""

from __future__ import annotations

from collections.abc import Callable
import time
from typing import Any


def compute_activity_envelope(
    audio: Any,
    sample_rate: int,
    *,
    window_seconds: float = 0.03,
) -> list[float]:
    """Retorna níveis 0..1 derivados do RMS real de cada janela do áudio."""
    try:
        import numpy as np
    except ImportError as error:
        raise RuntimeError("NumPy indisponível para envelope de TTS.") from error

    if sample_rate <= 0:
        raise ValueError("sample_rate deve ser positivo.")

    data = np.asarray(audio, dtype=np.float32)
    data = np.squeeze(data)
    if data.size == 0:
        return []

    if data.ndim > 1:
        data = data.mean(axis=-1)

    window_samples = max(1, int(sample_rate * window_seconds))
    rms_values: list[float] = []
    for start in range(0, len(data), window_samples):
        chunk = data[start : start + window_samples]
        if chunk.size == 0:
            continue
        rms = float(np.sqrt(np.mean(np.square(chunk, dtype=np.float64))))
        rms_values.append(rms)

    if not rms_values:
        return []

    nonzero = [value for value in rms_values if value > 1e-5]
    if not nonzero:
        return [0.0 for _ in rms_values]

    # O p90 mantém dinâmica relativa da frase e evita que um único pico
    # achate todo o restante da visualização.
    reference = max(0.015, float(np.percentile(nonzero, 90)))
    return [max(0.0, min(1.0, value / reference)) for value in rms_values]


def play_with_activity(
    sounddevice: Any,
    audio: Any,
    sample_rate: int,
    *,
    level_callback: Callable[[float], None] | None = None,
    window_seconds: float = 0.03,
    stop_requested: Callable[[], bool] | None = None,
) -> None:
    """Reproduz áudio e publica o envelope temporal alinhado ao playback."""
    sounddevice.play(audio, samplerate=sample_rate, blocking=False)
    if level_callback is None:
        if stop_requested is not None and stop_requested():
            sounddevice.stop()
        else:
            sounddevice.wait()
        return

    levels = compute_activity_envelope(
        audio,
        sample_rate,
        window_seconds=window_seconds,
    )
    started = time.monotonic()
    cancelled = False
    try:
        for index, level in enumerate(levels):
            if stop_requested is not None and stop_requested():
                cancelled = True
                try:
                    sounddevice.stop()
                except Exception:
                    pass
                break
            target = started + index * window_seconds
            delay = target - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            try:
                level_callback(level)
            except Exception:
                pass
        if not cancelled:
            sounddevice.wait()
    finally:
        try:
            level_callback(0.0)
        except Exception:
            pass
