from __future__ import annotations

from scripts.audit_stt_equivalence_23aj import _compare
from scripts.benchmark_stt_23aa import metrics
from jarvis.voice.semantic_shadow import sanitize_text


def _payload(mode: str, run: int, text: str = "Diminua o volume, por favor.") -> dict:
    row = {
        "id": "phrase_08", "path_name": "phrase_08.wav", "wav_sha256": "abc",
        "reference": "Diminua o volume.", "raw_text": text,
        "normalized": "diminua o volume por favor", "wer": .5, "cer": .2,
        "intent_slots": {"intent": "volume"}, "segments": [{"text": text}],
        "metadata": {"language": "pt"}, "forwarded_kwargs": {"beam_size": 1},
    }
    return {"mode": mode, "run": run, "environment": {"model": "same"},
            "options": {"beam_size": 1}, "order": ["phrase_08"], "rows": [row]}


def test_comparison_requires_text_segment_environment_options_and_order() -> None:
    payloads = [_payload("thread", run) for run in range(1, 4)]
    payloads += [_payload("process", run) for run in range(1, 4)]
    result = _compare(payloads)
    assert result["environment_equal"] is True
    assert result["options_equal"] is True
    assert result["order_equal"] is True
    assert result["all_37_texts_equal"] is True
    assert result["all_37_segments_equal"] is True
    assert result["rows"][0]["raw_text"].endswith("por favor.")


def test_23ai_sanitizer_cannot_be_used_for_exact_metric() -> None:
    reference = "Diminua o volume."
    candidate = "Diminua o volume, por favor."
    assert metrics(reference, candidate)[2] is False
    assert metrics(sanitize_text(reference), sanitize_text(candidate))[2] is True
