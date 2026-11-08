"""Offline inventory and fail-closed text adapter; never constructs or calls Tools."""

from __future__ import annotations

import hashlib
import importlib
import inspect
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.semantic_resolver_23ac import Catalog, parse
from jarvis.config import JarvisConfig
from jarvis.desktop.catalog import windows_default_catalog

CATALOG = ROOT / "docs/sprint-23ad-capability-catalog.json"
CANONICAL_SETTINGS = ROOT / "data/nonexistent-canonical-catalog-settings.json"
OPPOSITES = {
    "open": "close", "close": "open", "on": "off", "off": "on",
    "increase": "decrease", "decrease": "increase",
}
LANGUAGE = {
    "verbs": {
        "open": ["abra", "abre", "abrir"], "close": ["feche", "fecha", "fechar"],
        "on": ["ligue", "liga", "ligar"], "off": ["desligue", "desliga", "desligar"],
        "increase": ["aumente", "aumenta", "aumentar"],
        "decrease": ["diminua", "diminui", "diminuir"],
        "pause": ["pause", "pausa", "pausar"], "skip": ["pule", "pula", "pular"],
    },
    "opposites": OPPOSITES,
    "distinct_actions": [["pause", "skip"]],
    "negations": ["não", "nao", "nunca", "jamais"],
    "courtesy_terminal": ["por favor"],
    "numbers": {"cinco": 5, "quinze": 15, "cinquenta": 50},
    "units": ["minuto", "minutos"],
    "allowed_normalizations": ["case", "punctuation", "terminal_courtesy", "equal_number_value"],
    "forbidden": ["opposite_verb", "different_target", "missing_negation",
                  "different_number", "phonetic_guess", "unknown_application"],
}
READ_COMMAND_CONTRACT = {
    "version": 1,
    "leading_markers": ["e", "então", "Jarvis"],
    "politeness": ["por favor", "por gentileza"],
    "commands": [
        {"capability": "get_datetime", "action": "time",
         "aliases": ["que horas são", "que hora é", "qual é a hora"]},
        {"capability": "get_datetime", "action": "date",
         "aliases": ["qual é a data de hoje", "qual a data de hoje", "que dia é hoje"]},
        {"capability": "get_datetime", "action": "weekday",
         "aliases": ["qual é o dia da semana", "qual o dia da semana"]},
        {"capability": "get_runtime_info", "action": "runtime",
         "aliases": ["qual é o status do sistema", "qual é o status do runtime",
                     "qual é seu timeout", "você está em game mode"]},
    ],
}
LOCAL_CONTROL_CONTRACT = {
    "version": 1,
    "commands": [
        {"tool": "local_application_open", "arguments": {"application": "spotify"}, "aliases": ["abra o spotify", "abra spotify"]},
        {"tool": "local_application_open", "arguments": {"application": "obsidian"}, "aliases": ["abra o obsidian", "abra obsidian"]},
        {"tool": "local_application_open", "arguments": {"application": "default_browser"}, "aliases": ["abra o navegador", "abra navegador"]},
        {"tool": "local_application_status", "arguments": {"application": "spotify"}, "aliases": ["o spotify está aberto"]},
        {"tool": "local_application_status", "arguments": {"application": "obsidian"}, "aliases": ["o obsidian está aberto"]},
        {"tool": "local_application_focus", "arguments": {"application": "spotify"}, "aliases": ["traga o spotify para frente"]},
        {"tool": "local_application_focus", "arguments": {"application": "obsidian"}, "aliases": ["traga o obsidian para frente"]},
        {"tool": "local_application_close", "arguments": {"application": "spotify"}, "aliases": ["feche o spotify", "feche spotify"]},
        {"tool": "local_application_close", "arguments": {"application": "obsidian"}, "aliases": ["feche o obsidian", "feche obsidian"]},
        {"tool": "local_media_control", "arguments": {"action": "pause"}, "aliases": ["pause a música"]},
        {"tool": "local_media_control", "arguments": {"action": "play"}, "aliases": ["continue a música", "reproduza a música"]},
        {"tool": "local_media_control", "arguments": {"action": "next"}, "aliases": ["próxima faixa", "pule para a próxima faixa"]},
        {"tool": "local_media_control", "arguments": {"action": "previous"}, "aliases": ["faixa anterior", "volte para a faixa anterior"]},
        {"tool": "current_media", "arguments": {}, "aliases": ["o que está tocando", "qual música está tocando"]},
        {"tool": "local_volume_control", "arguments": {"action": "increase"}, "aliases": ["aumente o volume"]},
        {"tool": "local_volume_control", "arguments": {"action": "decrease"}, "aliases": ["diminua o volume"]},
        {"tool": "local_volume_control", "arguments": {"action": "set", "percent": 50}, "aliases": ["defina o volume para cinquenta por cento"]},
        {"tool": "local_volume_control", "arguments": {"action": "mute"}, "aliases": ["ative o mudo"]},
        {"tool": "local_volume_control", "arguments": {"action": "unmute"}, "aliases": ["desative o mudo"]}
    ]
}


def _class_for(source: str, name: str) -> type:
    parts = Path(source).with_suffix("").parts
    module = importlib.import_module(".".join(parts[1:]))
    matches = [value for value in vars(module).values()
               if inspect.isclass(value) and value.__module__ == module.__name__
               and getattr(value, "name", None) == name]
    if len(matches) != 1:
        raise ValueError(f"Tool implementation not uniquely found: {name}")
    return matches[0]


def _risk(name: str, declared: str) -> str:
    if name in {"local_application_close", "app_catalog_close"}:
        return "sensitive"
    if name in {"local_application_status", "current_media",
                "safe_browser_status", "safe_browser_destinations"}:
        return "read_only"
    if name == "safe_web_search":
        return "external_effect"
    if name == "safe_browser_open":
        return "reversible_local"
    if name.startswith("local_"):
        return "reversible_local"
    if name in {"delete_file", "obsidian_delete_note", "desktop_close_window",
                "browser_close_tab"}:
        return "destructive"
    if name.startswith("google_calendar_") and any(
        word in name for word in ("create", "update", "delete")
    ):
        return "external_effect"
    if name.startswith("browser_") and name not in {
        "browser_list_tabs", "browser_extract_text", "browser_extract_multiple",
        "browser_wait_for_selector", "browser_get_snapshot"
    }:
        return "external_effect"
    if name.startswith("desktop_") and name not in {
        "desktop_list_windows", "desktop_list_applications"
    }:
        return "external_effect"
    if name == "obsidian_open_note":
        return "external_effect"
    if name.startswith("vision_"):
        return "sensitive"
    if name in {"read_file", "obsidian_read_note", "obsidian_search_notes"}:
        return "sensitive"
    if declared in {"SAFE", "LOW", "READ_ONLY", "read_only"}:
        return "read_only"
    return "reversible_local"


def _actions(name: str) -> list[str]:
    tokens = name.split("_")
    verb = next((word for word in tokens if word in {
        "get", "list", "read", "create", "edit", "move", "delete", "open", "close",
        "focus", "press", "switch", "navigate", "click", "type", "fill", "extract",
        "wait", "observe", "search", "append", "update", "check"
    }), name)
    return [verb]


def _target(name: str, applications: set[str]) -> dict:
    if name.startswith('organization_'):
        return {'kind': 'sealed_plan_selected_root_rules', 'values': [], 'aliases': {}}
    if name == "desktop_open_application":
        source = windows_default_catalog()
        return {"kind": "allowlist", "values": sorted(applications),
                "aliases": dict(sorted(source._aliases.items()))}
    if name.startswith("desktop_"):
        return {"kind": "bound_window_or_allowlist", "values": [], "aliases": {}}
    if name.startswith("browser_"):
        return {"kind": "bound_tab_domain_or_element", "values": [], "aliases": {}}
    if name.startswith("google_calendar_"):
        return {"kind": "configured_calendar", "values": [], "aliases": {}}
    if name.startswith("obsidian_"):
        return {"kind": "configured_vault", "values": [], "aliases": {}}
    if name.startswith("vision_"):
        return {"kind": "screen_or_bound_window", "values": [], "aliases": {}}
    if name in {"get_datetime", "get_runtime_info"}:
        return {"kind": "local_context", "values": [], "aliases": {}}
    if name in {"current_media", "local_media_control"}:
        return {"kind": "windows_current_media_session", "values": [], "aliases": {}}
    if name.startswith("safe_browser_") or name == "safe_web_search":
        return {"kind": "registered_https_destination", "values": [], "aliases": {}}
    return {"kind": "filesystem_guard_base", "values": [], "aliases": {}}


def _reversibility(name: str, risk: str) -> str:
    if risk in {"read_only", "sensitive"}:
        return "no_mutation"
    if risk == "destructive":
        return "not_guaranteed; may lose data"
    if name in {"create_file", "obsidian_create_note", "browser_new_tab"}:
        return "compensating_delete_or_close_possible; not automatic"
    if name in {"move_file", "edit_file", "obsidian_append_note"}:
        return "requires original state or backup; not automatic"
    return "operation_dependent; not guaranteed"


def _schema(cls: type, name: str, applications: set[str]) -> dict:
    if name.startswith("app_catalog_"):
        return cls(None).parameters
    if name.startswith("local_application_"):
        return {"type": "object", "properties": {
            "application": {"type": "string", "enum": [
                "default_browser", "obsidian", "spotify"]}},
            "required": ["application"], "additionalProperties": False}
    if name == "desktop_open_application":
        return {"type": "object", "properties": {
            "application": {"type": "string", "enum": sorted(applications)}},
            "required": ["application"], "additionalProperties": False}
    value = getattr(cls, "parameters", None)
    if not isinstance(value, dict):
        raise ValueError(f"Missing parameter schema: {name}")
    return value


def inventory() -> dict:
    source = Catalog.from_checkout(JarvisConfig(settings_path=CANONICAL_SETTINGS))
    test_files = list((ROOT / "tests").rglob("test_*.py"))
    tests = {path.relative_to(ROOT).as_posix(): path.read_text(encoding="utf-8")
             for path in test_files}
    rows = []
    for name, meta in sorted(source.tools.items()):
        cls = _class_for(meta.source, name)
        schema = _schema(cls, name, source.applications)
        references = sorted(path for path, content in tests.items() if name in content)
        risk = _risk(name, meta.risk)
        rows.append({
            "id": name, "human_name": name.replace("_", " ").title(),
            "description": cls.description, "tool": name, "implementation": meta.source,
            "implementation_proven": True, "source_risk": meta.risk,
            "risk": risk, "actions": _actions(name),
            "targets": _target(name, source.applications), "aliases": [],
            "parameters": schema, "return": "Tool.execute returns text; success requires verification",
            "side_effects": risk not in {"read_only", "sensitive"},
            "reversibility": _reversibility(name, risk),
            "network": name.startswith(("browser_", "google_calendar_", "google_drive_", "google_docs_"))
            or name == "safe_web_search",
            "gui": name.startswith(("desktop_", "browser_", "obsidian_open", "vision_"))
            or name.startswith("safe_browser_") or name == "safe_web_search",
            "availability": "enabled_by_config" if meta.enabled else "disabled_by_config",
            "readiness_proven": name in {
                "get_datetime", "get_runtime_info", "local_application_open",
                "local_application_status", "local_application_focus",
                "local_application_close", "current_media", "local_media_control",
                "local_volume_control", "safe_browser_status",
                "safe_browser_destinations", "safe_browser_open", "safe_web_search",
            },
            "state": "implemented_tested" if references else "implemented_partial",
            "tests": references, "confirmation": "required_by_policy"
            if name in {"google_calendar_create_event", "google_calendar_update_event"} or name.startswith('organization_')
            else "required_by_proposal" if name == "safe_web_search" else "not_required_by_proposal",
            "runtime_confirmation": (
                "external_effect; prepared account/calendar/payload/version binding; one-use executor receipt"
                if name in {"google_calendar_create_event", "google_calendar_update_event"}
                else "default ask; selected root/identity/rules/plan/policy binding; persistent allow only for this scope"
                if name.startswith('organization_')
                else "CRITICAL_only_by_default; other policy gates still apply"
            ),
        })
    return {
        "schema_version": 1, "read_command_contract": READ_COMMAND_CONTRACT,
        "local_control_contract": LOCAL_CONTROL_CONTRACT,
        "source_sha256": source.snapshot()["source_sha256"],
        "language": LANGUAGE, "capabilities": rows,
        "semantic_read_dispatch": {
            "enabled_by_default": False,
            "eligible": ["get_datetime", "get_runtime_info"],
            "blocked_read_only": {
                "list_files": "readiness_not_proven_and_arbitrary_files_forbidden",
                "desktop_list_applications": "disabled_and_desktop_forbidden",
                "desktop_list_windows": "disabled_and_desktop_forbidden",
                "browser_extract_multiple": "network_or_browser_forbidden",
                "browser_extract_text": "network_or_browser_forbidden",
                "browser_get_snapshot": "network_or_browser_forbidden",
                "browser_list_tabs": "network_or_browser_forbidden",
                "browser_wait_for_selector": "network_or_browser_forbidden",
                "google_calendar_check_availability": "external_service_forbidden",
                "google_calendar_get_event": "external_service_forbidden",
                "google_calendar_list_events": "external_service_forbidden",
                "obsidian_list_notes": "personal_files_forbidden",
            },
        },
        "planned": [],
        "not_implemented": ["light_control",
                            "timer_control", "shopping_list"],
    }


def validate(data: dict) -> None:
    if data.get("schema_version") != 1:
        raise ValueError("unsupported schema version")
    canonical = Catalog.from_checkout(JarvisConfig(settings_path=CANONICAL_SETTINGS))
    if data.get("source_sha256") != canonical.snapshot()["source_sha256"]:
        raise ValueError("catalog source drift")
    rows = data.get("capabilities")
    if not isinstance(rows, list):
        raise ValueError("capabilities must be an array")
    ids = [row.get("id") for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate capability id")
    aliases: dict[str, str] = {}
    for row in rows:
        name = row["id"]
        if not row.get("risk") or row["risk"] not in {
            "read_only", "reversible_local", "external_effect", "sensitive", "destructive"
        }:
            raise ValueError(f"action without valid risk: {name}")
        if not row.get("actions"):
            raise ValueError(f"action missing: {name}")
        if row.get("availability") == "enabled_by_config" and not row.get("implementation_proven"):
            raise ValueError(f"available without proven implementation: {name}")
        if row.get("implementation_proven") and not (ROOT / row.get("implementation", "")).is_file():
            raise ValueError(f"implementation source missing: {name}")
        schema = row.get("parameters", {})
        if schema.get("type") != "object":
            raise ValueError(f"invalid parameter object: {name}")
        props = schema.get("properties", {})
        for parameter, spec in props.items():
            if not isinstance(spec, dict) or not spec.get("type"):
                raise ValueError(f"parameter without type: {name}.{parameter}")
        if not set(schema.get("required", [])).issubset(props):
            raise ValueError(f"required parameter not declared: {name}")
        target = row.get("targets", {})
        values = target.get("values", [])
        for target_alias, canonical in target.get("aliases", {}).items():
            if target_alias in values or canonical not in values:
                raise ValueError(f"conflicting or unknown target alias: {name}.{target_alias}")
        for alias in [name, *row.get("aliases", [])]:
            normalized = alias.casefold().strip()
            if not normalized:
                raise ValueError(f"empty alias: {name}")
            if normalized in aliases and aliases[normalized] != name:
                raise ValueError(f"conflicting alias: {alias}")
            aliases[normalized] = name
    language = data["language"]
    verbs = language["verbs"]
    for action, other in language["opposites"].items():
        if action not in verbs or other not in verbs or language["opposites"].get(other) != action:
            raise ValueError(f"missing opposite pair: {action}")
    for left, right in language["distinct_actions"]:
        if left not in verbs or right not in verbs or left == right:
            raise ValueError(f"invalid distinct pair: {left}/{right}")
    spoken: dict[str, str] = {}
    for action, words in verbs.items():
        for word in words:
            if word in spoken and spoken[word] != action:
                raise ValueError(f"conflicting verb alias: {word}")
            spoken[word] = action


def decide_text(text: str, data: dict) -> str:
    """23AC adapter: conservative verdict only, no Tool object or execution."""
    validate(data)
    meaning = parse(text)
    if meaning.negated or meaning.ambiguous or not meaning.action or not meaning.target:
        return "reject"
    if re.search(r"\b(?:e|depois|então)\b", text.casefold()) and meaning.intent != "datetime":
        return "reject"
    if meaning.intent == "datetime":
        row = next((item for item in data["capabilities"] if item["id"] == "get_datetime"), None)
        if row and row["availability"] == "enabled_by_config" and row["risk"] == "read_only":
            return "diagnostic_read_candidate"
    if meaning.intent == "application" and meaning.action == "open":
        row = next((item for item in data["capabilities"]
                    if item["id"] == "desktop_open_application"), None)
        if row:
            canonical = row["targets"]["aliases"].get(meaning.target, meaning.target)
        else:
            canonical = None
        if row and row["availability"] == "enabled_by_config" and canonical in row["targets"]["values"]:
            return "confirm"
    return "reject"


def main() -> None:
    data = inventory()
    validate(data)
    CATALOG.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{len(data['capabilities'])} registered Tools; sha256={hashlib.sha256(CATALOG.read_bytes()).hexdigest()}")


if __name__ == "__main__":
    main()
