import io
import json

from jarvis.speech.ptbr_fallback import (
    PtBrFallbackClient,
    PtBrFallbackResult,
)
from jarvis.speech import ptbr_fallback_worker


def test_high_confidence_ctc_result_is_accepted() -> None:
    fallback = PtBrFallbackClient(
        minimum_confidence=0.90,
        minimum_margin=5.0,
    )
    result = PtBrFallbackResult(
        "QUE ORAS SÃO",
        confidence=0.979,
        margin=6.94,
        duration_seconds=1.85,
    )

    assert fallback.accepts(result) is True


def test_observed_false_positive_quedas_is_not_accepted_by_default() -> None:
    fallback = PtBrFallbackClient()
    result = PtBrFallbackResult(
        "QUEDAS SÃO",
        confidence=0.916,
        margin=6.264,
        duration_seconds=1.93,
    )

    assert fallback.accepts(result) is False


def test_observed_false_positive_em_sou_eu_is_not_accepted_by_default() -> None:
    fallback = PtBrFallbackClient()
    result = PtBrFallbackResult(
        "EM SOU EU",
        confidence=0.936,
        margin=6.875,
        duration_seconds=2.24,
    )

    assert fallback.accepts(result) is False


def test_low_confidence_complementary_decode_is_not_accepted() -> None:
    fallback = PtBrFallbackClient(
        minimum_confidence=0.90,
        minimum_margin=5.0,
    )
    result = PtBrFallbackResult(
        "ABRA O GITERHAME",
        confidence=0.793,
        margin=4.20,
        duration_seconds=2.25,
    )

    assert fallback.accepts(result) is False


def test_worker_protocol_is_ascii_safe_and_restores_unicode(
    monkeypatch,
) -> None:
    buffer = io.StringIO()
    monkeypatch.setattr(ptbr_fallback_worker.sys, "stdout", buffer)

    ptbr_fallback_worker._emit(
        {"type": "result", "text": "QUE HORAS SÃO"}
    )

    payload = buffer.getvalue()
    payload.encode("ascii")
    assert json.loads(payload)["text"] == "QUE HORAS SÃO"


def test_empty_fallback_transcript_is_never_accepted() -> None:
    fallback = PtBrFallbackClient()
    result = PtBrFallbackResult(
        "",
        confidence=0.999,
        margin=20.0,
        duration_seconds=1.0,
    )

    assert fallback.accepts(result) is False
