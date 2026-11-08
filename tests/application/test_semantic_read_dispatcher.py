from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from jarvis.application.semantic_read_dispatcher import (
    SemanticReadDispatcher, _ResolvedIntent,
)
from jarvis.security.risk import RiskLevel
from jarvis.tools.execution_result import ToolExecutionResult
from jarvis.tools.get_datetime import GetDateTimeTool
from jarvis.tools.registry import ToolRegistry


def catalog(*, runtime: bool = False) -> dict:
    names = ["get_datetime"] + (["get_runtime_info"] if runtime else [])
    commands = [
        {"capability": "get_datetime", "action": "time",
         "aliases": ["que horas são"]},
        {"capability": "get_datetime", "action": "date",
         "aliases": ["qual é a data de hoje", "qual a data de hoje", "que dia é hoje"]},
        {"capability": "get_datetime", "action": "weekday",
         "aliases": ["qual é o dia da semana"]},
    ]
    if runtime:
        commands.append({"capability": "get_runtime_info", "action": "runtime",
                         "aliases": ["qual é o status do runtime"]})
    return {"read_command_contract": {
        "version": 1, "leading_markers": ["e", "então", "Jarvis"],
        "politeness": ["por favor", "por gentileza"], "commands": commands,
    }, "capabilities": [
        {
            "id": name, "tool": name, "risk": "read_only",
            "source_risk": "READ_ONLY", "availability": "enabled_by_config",
            "implementation_proven": True, "readiness_proven": True,
            "state": "implemented_tested", "side_effects": False,
            "network": False, "gui": False, "tests": ["test.py"],
            "parameters": {"type": "object", "properties": {},
                           "required": [], "additionalProperties": False},
        } for name in names
    ]}


class ReadTool(GetDateTimeTool):
    name = "get_runtime_info"

    def execute(self, **kwargs: object) -> str:
        return "Estado: IDLE\nSTT: large-v3/process"


class CountingExecution:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict, str | None]] = []

    def __call__(self, tool, arguments, _grant, interaction_id):
        self.calls.append((tool.name, dict(arguments), interaction_id))
        return ToolExecutionResult(tool.execute(**arguments), "success", True)


def make_dispatcher(*, custom_catalog: dict | None = None,
                    include_runtime: bool = True, **kwargs):
    registry = ToolRegistry()
    clock = datetime(2026, 9, 28, 14, 7,
                     tzinfo=timezone(timedelta(hours=-3)))
    registry.register(GetDateTimeTool(clock=lambda: clock))
    if include_runtime:
        registry.register(ReadTool(clock=lambda: clock))
    execution = CountingExecution()
    dispatcher = SemanticReadDispatcher(
        registry, execution, catalog=custom_catalog or catalog(runtime=include_runtime),
        **kwargs,
    )
    return dispatcher, execution


@pytest.mark.parametrize(("text", "fragment"), [
    ("Que horas são?", "14 horas e 7 minutos"),
    ("Qual é a data de hoje?", "28 de setembro de 2026"),
    ("Qual é o dia da semana?", "segunda-feira"),
])
def test_valid_datetime_intents_execute_exactly_once(text: str, fragment: str) -> None:
    dispatcher, execution = make_dispatcher()
    interaction = dispatcher.new_interaction_id()
    first = dispatcher.dispatch(text, interaction_id=interaction)
    repeated = dispatcher.dispatch(text, interaction_id=interaction)
    assert first.executed and repeated == first and fragment in (first.response or "")
    assert execution.calls == [("get_datetime", {}, interaction)]


@pytest.mark.parametrize("text", [
    "E que horas são?", "Jarvis, que horas são, por favor?",
    "  QUE   HORAS SÃO!!! ", "Qual a data de hoje?", "Que dia é hoje?",
])
def test_declared_safe_variants_execute(text: str) -> None:
    dispatcher, execution = make_dispatcher()
    result = dispatcher.dispatch(text)
    assert result.executed and result.tool_name == "get_datetime"
    assert len(execution.calls) == 1


def test_runtime_info_is_the_only_other_eligible_local_tool() -> None:
    dispatcher, execution = make_dispatcher()
    result = dispatcher.dispatch("Qual é o status do runtime?")
    assert dispatcher.eligible_tools == ("get_datetime", "get_runtime_info")
    assert result.executed and result.tool_name == "get_runtime_info"
    assert len(execution.calls) == 1


@pytest.mark.parametrize("text", [
    "Não me diga que horas são", "Qual é a data", "get_datatime",
    "execute get_datetime", "Que horas são e qual é a data de hoje?", "",
    "Qual a data dele?", "E aderção", "Que odes são?", "Não diga as horas",
    "Abra get_datetime", "Que horas são texto residual", "Sem dizer que horas são",
])
def test_ambiguous_negated_partial_named_or_empty_text_never_executes(text: str) -> None:
    dispatcher, execution = make_dispatcher()
    result = dispatcher.dispatch(text)
    assert not result.executed
    assert execution.calls == []


@pytest.mark.parametrize("override", [
    {"requested_tool": "get_datetime"},
    {"requested_tool": "get_datatime"},
    {"claimed_risk": "read_only"},
    {"claimed_risk": "reversible_local"},
    {"arguments": {"extra": True}},
    {"arguments": {"kind": 7}},
])
def test_authority_or_argument_overrides_are_rejected(override: dict) -> None:
    dispatcher, execution = make_dispatcher()
    result = dispatcher.dispatch("Que horas são?", **override)
    assert result.handled and not result.executed and execution.calls == []


@pytest.mark.parametrize("mutation", [
    lambda row: row.update(risk="reversible_local"),
    lambda row: row.update(readiness_proven=False),
    lambda row: row.update(availability="disabled_by_config"),
    lambda row: row.update(tool="get_datatime"),
    lambda row: row.update(parameters={"type": "object"}),
])
def test_catalog_divergence_fails_closed(mutation) -> None:
    changed = deepcopy(catalog())
    mutation(changed["capabilities"][0])
    dispatcher, execution = make_dispatcher(
        custom_catalog=changed, include_runtime=False,
    )
    result = dispatcher.dispatch("Que horas são?")
    assert not result.executed and dispatcher.eligible_tools == ()
    assert execution.calls == []


def test_missing_tool_and_runtime_risk_mismatch_fail_closed() -> None:
    missing, missing_execution = make_dispatcher(include_runtime=False)
    unavailable = missing.dispatch("Qual é o status do runtime?")
    assert not unavailable.executed and missing_execution.calls == []

    dispatcher, execution = make_dispatcher()
    dispatcher._registry.get("get_runtime_info").risk = RiskLevel.EXTERNAL_EFFECT
    mismatch = dispatcher.dispatch("Qual é o status do runtime?")
    assert not mismatch.executed and execution.calls == []


def test_resolution_timeout_and_late_decision_never_execute() -> None:
    now = [0.0]
    def late(_text: str) -> _ResolvedIntent:
        now[0] = 1.0
        return _ResolvedIntent(True, "get_datetime", "time", {}, "resolved")
    dispatcher, execution = make_dispatcher(
        clock=lambda: now[0], resolver=late, timeout_seconds=.1,
    )
    result = dispatcher.dispatch("Que horas são?")
    assert result.reason == "resolution_timeout" and not result.executed
    assert execution.calls == []


def test_normalizer_exception_fails_closed() -> None:
    def broken(_text: str) -> _ResolvedIntent:
        raise RuntimeError("injected")
    dispatcher, execution = make_dispatcher(resolver=broken)
    result = dispatcher.dispatch("Que horas são?")
    assert result.reason == "normalization_error" and not result.executed
    assert execution.calls == []


def test_missing_command_contract_fails_closed() -> None:
    broken = catalog()
    broken.pop("read_command_contract")
    dispatcher, execution = make_dispatcher(custom_catalog=broken, include_runtime=False)
    result = dispatcher.dispatch("Que horas são?")
    assert result.reason == "catalog_unavailable" and not result.executed
    assert execution.calls == []


def test_replay_from_previous_process_generation_is_rejected() -> None:
    old, old_execution = make_dispatcher()
    interaction = old.new_interaction_id()
    assert old.dispatch("Que horas são?", interaction_id=interaction).executed
    fresh, fresh_execution = make_dispatcher()
    replay = fresh.dispatch("Que horas são?", interaction_id=interaction)
    assert replay.reason == "stale_interaction" and not replay.executed
    assert len(old_execution.calls) == 1 and fresh_execution.calls == []


@pytest.mark.parametrize("transcript", [
    "Qual a data dele?", "E a lição.", "E olha só.",
    "Fica a atenção.", "e adeçam", "Abro o Spotify.", "Pausa a música.",
    "Olhe para a próxima faixa.", "Diminua o volume, por favor.",
])
def test_known_incorrect_23ab_23ah_transcripts_never_execute(transcript: str) -> None:
    dispatcher, execution = make_dispatcher()
    assert not dispatcher.dispatch(transcript).executed
    assert execution.calls == []
