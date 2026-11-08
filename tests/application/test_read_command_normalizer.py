from __future__ import annotations

from copy import deepcopy

import pytest

from jarvis.application.read_command_normalizer import ReadCommandNormalizer


def contract() -> dict:
    return {
        "version": 1,
        "leading_markers": ["e", "então", "Jarvis"],
        "politeness": ["por favor", "por gentileza"],
        "commands": [
            {"capability": "get_datetime", "action": "time",
             "aliases": ["que horas são"]},
            {"capability": "get_datetime", "action": "date",
             "aliases": ["qual é a data de hoje", "qual a data de hoje"]},
            {"capability": "get_runtime_info", "action": "runtime",
             "aliases": ["qual é o status do runtime"]},
        ],
    }


def normalizer(value: dict | None = None) -> ReadCommandNormalizer:
    return ReadCommandNormalizer(
        value or contract(), tool_names={"get_datetime", "get_runtime_info"},
    )


def test_output_preserves_audit_fields_and_safe_transformations() -> None:
    result = normalizer().normalize("  JARVIS, que horas SÃO, por favor?! ")
    assert result.sanitized_original == "JARVIS, que horas SÃO, por favor?!"
    assert result.normalized_text == "que horas sao"
    assert result.preserved_tokens == ("jarvis", "que", "horas", "sao", "por", "favor")
    assert {"casefold", "remove_diacritics", "punctuation", "leading_marker",
            "politeness"} <= set(result.transformations)
    assert result.rejection_reason is None and result.tool_name == "get_datetime"


@pytest.mark.parametrize("text,reason", [
    ("não diga que horas são", "explicit_negation"),
    ("nunca que horas são", "explicit_negation"),
    ("sem que horas são", "explicit_negation"),
    ("que horas são e a data", "incomplete_or_residual_text"),
    ("que horas são depois", "incomplete_or_residual_text"),
    ("qual é a data", "incomplete_or_residual_text"),
    ("que odes são", "incomplete_or_residual_text"),
    ("", "empty_transcript"),
    ("Que horas sÃ£o?", "invalid_unicode"),
    ("x" * 501, "text_too_long"),
])
def test_forbidden_or_invalid_inputs_fail_closed(text: str, reason: str) -> None:
    result = normalizer().normalize(text)
    assert result.rejection_reason == reason and result.tool_name is None


@pytest.mark.parametrize("mutate", [
    lambda c: c.update(version=2),
    lambda c: c["commands"].append({"capability": "get_runtime_info", "action": "x",
                                     "aliases": ["que horas são"]}),
    lambda c: c["commands"].append({"capability": "get_datetime", "action": "x",
                                     "aliases": ["get_datetime"]}),
    lambda c: c["commands"].append({"capability": "get_datetime", "action": "x",
                                     "aliases": ["não diga as horas"]}),
    lambda c: c["commands"].append({"capability": "get_datetime", "action": "x",
                                     "aliases": ["e"]}),
    lambda c: c["commands"].append({"capability": "get_datetime", "action": "x",
                                     "aliases": ["e informe as horas"]}),
])
def test_contract_collisions_and_invalid_aliases_fail_initialization(mutate) -> None:
    changed = deepcopy(contract())
    mutate(changed)
    with pytest.raises(ValueError):
        normalizer(changed)
