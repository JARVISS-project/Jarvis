from scripts.benchmark_stt_23x import CONFIGS, normalize, scores


def test_portuguese_metrics_preserve_accents_and_remove_punctuation() -> None:
    assert normalize(" QUE HORAS SÃO?! ") == "que horas são"
    assert scores("Que horas são?") == (0.0, 0.0)
    wer, cer = scores("Que oras são")
    assert wer == 1 / 3
    assert cer > 0


def test_core_matrix_changes_one_variable_from_live() -> None:
    baseline = CONFIGS["live_medium"]
    assert CONFIGS["explicit_pt_control"] == baseline
    for name in ("temperature_zero", "beam_five", "vad_off", "language_auto",
                 "tiny_model", "small_model", "large_v3_model", "turbo_model"):
        assert sum(CONFIGS[name][key] != baseline[key] for key in baseline) == 1
