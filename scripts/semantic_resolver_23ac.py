"""Offline semantic command resolver. It never invokes a JARVIS Tool."""

from __future__ import annotations

import ast
from dataclasses import asdict, dataclass, field
from difflib import get_close_matches
import hashlib
import json
from pathlib import Path
import re
import sys
import time
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from jarvis.config import JarvisConfig
from jarvis.desktop.catalog import windows_default_catalog
from jarvis.runtime.fast_path import FastPathRouter


VERBS = {
    "abra": ("application", "open"), "abre": ("application", "open"),
    "abrir": ("application", "open"),
    "feche": ("application", "close"), "fecha": ("application", "close"),
    "fechar": ("application", "close"),
    "pause": ("media", "pause"), "pausa": ("media", "pause"),
    "pausar": ("media", "pause"),
    "pule": ("media", "skip"), "pula": ("media", "skip"),
    "pular": ("media", "skip"),
    "reproduza": ("media", "play"), "reproduzir": ("media", "play"),
    "aumente": ("volume", "increase"), "aumenta": ("volume", "increase"),
    "aumentar": ("volume", "increase"),
    "diminua": ("volume", "decrease"), "diminui": ("volume", "decrease"),
    "diminuir": ("volume", "decrease"),
    "ligue": ("automation", "on"), "liga": ("automation", "on"),
    "ligar": ("automation", "on"),
    "desligue": ("automation", "off"), "desliga": ("automation", "off"),
    "desligar": ("automation", "off"),
    "defina": ("timer", "set"), "define": ("timer", "set"),
    "definir": ("timer", "set"),
    "adicione": ("shopping_list", "add"), "adiciona": ("shopping_list", "add"),
    "adicionar": ("shopping_list", "add"),
}
NUMBERS = {"zero": 0, "um": 1, "uma": 1, "cinco": 5, "quinze": 15, "cinquenta": 50}
NEGATIONS = {"não", "nao", "nunca", "jamais"}


@dataclass(frozen=True)
class Meaning:
    intent: str | None = None
    action: str | None = None
    target: str | None = None
    parameters: dict[str, object] = field(default_factory=dict)
    negated: bool = False
    ambiguous: bool = False
    phonetic_hints: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ToolMeta:
    name: str
    risk: str
    source: str
    enabled: bool
    availability: str


@dataclass(frozen=True)
class Resolution:
    outcome: str
    reason: str
    tool: str | None
    meaning: Meaning
    elapsed_ms: float

    def as_dict(self) -> dict:
        return {"outcome": self.outcome, "reason": self.reason, "tool": self.tool,
                "meaning": self.meaning.as_dict(), "elapsed_ms": self.elapsed_ms}


def normalize(text: str) -> str:
    """Remove only punctuation/case and terminal courtesy, retaining accents."""
    value = unicodedata.normalize("NFKC", text).casefold()
    value = re.sub(r"[^\w\sÀ-ÿ]", " ", value)
    value = " ".join(value.split())
    return re.sub(r"(?:\s+por favor)$", "", value)


def _number(value: str) -> int | None:
    return int(value) if value.isdecimal() else NUMBERS.get(value)


def parse(text: str) -> Meaning:
    value = normalize(text)
    words = value.split()
    negated = any(word in NEGATIONS for word in words)
    clean = " ".join(word for word in words if word not in NEGATIONS)
    if not clean:
        return Meaning(negated=negated)

    # Reuse the registered fast-path domain grammar without its STT repairs.
    fast = FastPathRouter.normalize(clean)
    if any(pattern.fullmatch(fast) for pattern in FastPathRouter._TIME_PATTERNS):
        return Meaning("datetime", "read_time", "local_clock", {"kind": "time"}, negated)
    if any(pattern.fullmatch(fast) for pattern in FastPathRouter._DATE_PATTERNS):
        return Meaning("datetime", "read_date", "local_clock", {"kind": "date"}, negated)

    match = re.fullmatch(r"quem (?:é|e) o autor de (.+)", clean)
    if match:
        return Meaning("knowledge", "ask_author", match.group(1), negated=negated)
    match = re.fullmatch(r"qual a (?:diferença|diferenca) entre (.+?) e (.+)", clean)
    if match:
        left, right = match.groups()
        return Meaning("comparison", "compare", "terms", {"left": left, "right": right},
                       negated, ambiguous=left == right)

    first, _, rest = clean.partition(" ")
    operation = VERBS.get(first)
    if operation is None:
        hints = tuple(get_close_matches(first, VERBS, n=3, cutoff=0.72))
        return Meaning(negated=negated, ambiguous=bool(hints), phonetic_hints=hints)
    intent, action = operation
    rest = re.sub(r"^(?:o|a|os|as)\s+", "", rest)
    if intent == "application":
        return Meaning(intent, action, rest or None, negated=negated,
                       ambiguous=not bool(rest))
    if intent == "volume":
        target = "volume" if rest == "volume" else (rest or None)
        return Meaning(intent, action, target, negated=negated,
                       ambiguous=target != "volume")
    if intent == "media":
        if action == "pause":
            target = "music" if rest in {"música", "musica"} else (rest or None)
            return Meaning(intent, action, target, negated=negated,
                           ambiguous=target != "music")
        if action == "skip":
            target = "next_track" if rest in {"para a próxima faixa", "para a proxima faixa",
                                                "próxima faixa", "proxima faixa"} else (rest or None)
            return Meaning(intent, action, target, negated=negated,
                           ambiguous=target != "next_track")
        return Meaning(intent, action, rest or None, negated=negated, ambiguous=True)
    if intent == "automation":
        match = re.fullmatch(r"luzes da (.+)", rest)
        target = f"lights:{match.group(1)}" if match else (rest or None)
        return Meaning(intent, action, target, negated=negated, ambiguous=match is None)
    if intent == "timer":
        match = re.fullmatch(r"um timer de (\w+) minutos?", rest)
        number = _number(match.group(1)) if match else None
        return Meaning(intent, action, "timer" if match else (rest or None),
                       {"minutes": number} if number is not None else {}, negated,
                       ambiguous=number is None)
    if intent == "shopping_list":
        match = re.fullmatch(r"(.+?) (?:à|a) lista de compras", rest)
        return Meaning(intent, action, "shopping_list" if match else (rest or None),
                       {"item": match.group(1)} if match else {}, negated,
                       ambiguous=match is None)
    raise AssertionError(f"Unhandled intent {intent}")


def _factory_classes(module: str, function: str) -> list[str]:
    path = ROOT / "src" / Path(*module.split(".")).with_suffix(".py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    factory = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                   and node.name == function)
    names = []
    for node in ast.walk(factory):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.List):
            names.extend(item.func.id for item in node.value.elts
                         if isinstance(item, ast.Call) and isinstance(item.func, ast.Name))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id.startswith(("LocalApplication", "LocalMedia", "LocalVolume")):
                names.append(node.func.id)
    return sorted(set(names))


def _metadata(module: str, class_name: str) -> tuple[str, str, str]:
    path = ROOT / "src" / Path(*module.split(".")).with_suffix(".py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    definition = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                      and node.name == class_name)
    name = risk = None
    for node in definition.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "name":
                name = ast.literal_eval(node.value)
            if isinstance(target, ast.Name) and target.id == "risk":
                risk = node.value.attr if isinstance(node.value, ast.Attribute) else None
    if not isinstance(name, str) or not isinstance(risk, str):
        raise RuntimeError(f"Missing Tool metadata: {module}.{class_name}")
    return name, risk, str(path.relative_to(ROOT))


def _factory_control_names(tree: ast.AST, factory_name: str) -> set[str]:
    """Read the Tool-name capability split from build_harness itself."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.For) or not isinstance(node.iter, ast.Call):
            continue
        if not isinstance(node.iter.func, ast.Name) or node.iter.func.id != factory_name:
            continue
        for child in ast.walk(node):
            if not isinstance(child, ast.Compare) or not isinstance(child.left, ast.Attribute):
                continue
            if child.left.attr == "name" and isinstance(child.ops[0], ast.In):
                if isinstance(child.comparators[0], ast.Set):
                    return {ast.literal_eval(item) for item in child.comparators[0].elts}
    return set()


def _capability_settings(tree: ast.AST) -> dict[str, str]:
    """Read Capability-to-config bindings from the real CapabilityPolicy call."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id != "CapabilityPolicy" or not node.args or not isinstance(node.args[0], ast.Dict):
            continue
        return {
            key.attr: value.attr
            for key, value in zip(node.args[0].keys, node.args[0].values)
            if isinstance(key, ast.Attribute) and isinstance(value, ast.Attribute)
        }
    raise RuntimeError("CapabilityPolicy configuration was not found")


def _enclosing_capability(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> str | None:
    current = parents.get(node)
    while current is not None:
        if isinstance(current, ast.If):
            for child in ast.walk(current.test):
                if isinstance(child, ast.Attribute) and isinstance(child.value, ast.Name):
                    if child.value.id == "Capability":
                        return child.attr
        current = parents.get(current)
    return None


def _enclosing_service(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> str | None:
    current = parents.get(node)
    while current is not None:
        if isinstance(current, ast.If) and isinstance(current.test, ast.Compare):
            test = current.test
            if isinstance(test.left, ast.Name) and any(isinstance(op, ast.IsNot) for op in test.ops):
                return test.left.id
        current = parents.get(current)
    return None


class Catalog:
    """Read-only view of Tool classes reached by build_harness registrations."""

    def __init__(self, tools: dict[str, ToolMeta], applications: set[str]) -> None:
        self.tools = tools
        self.applications = applications

    def active(self, name: str) -> bool:
        return bool(name in self.tools and self.tools[name].enabled)

    @classmethod
    def from_checkout(cls, config: JarvisConfig | None = None) -> Catalog:
        config = config or JarvisConfig()
        path = ROOT / "src/jarvis/__main__.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports: dict[str, str] = {}
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module:
                for alias in node.names:
                    imports[alias.asname or alias.name] = node.module

        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        capability_settings = _capability_settings(tree)
        classes: list[tuple[str, str, str | None, str | None]] = []
        factories: list[tuple[str, str]] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "register" and isinstance(node.func.value, ast.Name):
                    if node.func.value.id == "tool_registry" and node.args:
                        argument = node.args[0]
                        if isinstance(argument, ast.Call) and isinstance(argument.func, ast.Name):
                            symbol = argument.func.id
                            classes.append((imports[symbol], symbol,
                                            _enclosing_capability(node, parents),
                                            _enclosing_service(node, parents)))
            if isinstance(node, ast.For) and isinstance(node.iter, ast.Call):
                function = node.iter.func
                if isinstance(function, ast.Name) and function.id in {
                    "desktop_tools", "browser_tools", "obsidian_tools",
                    "local_control_tools", "media_tools", "safe_browser_tools", "application_catalog_tools",
                }:
                    factories.append((imports[function.id], function.id))
        for module, function in factories:
            classes.extend((module, name, None, None) for name in _factory_classes(module, function))

        control_names = _factory_control_names(tree, "desktop_tools")
        obsidian_write = _factory_control_names(tree, "obsidian_tools")
        tools: dict[str, ToolMeta] = {}
        for module, class_name, capability, service in classes:
            name, risk, source = _metadata(module, class_name)
            if capability is not None:
                setting = capability_settings[capability]
            elif name.startswith("desktop_"):
                setting = "desktop_control_enabled" if name in control_names else "desktop_read_enabled"
            elif name.startswith("browser_"):
                setting = "browser_enabled"
            elif name.startswith("obsidian_"):
                setting = "obsidian_write_enabled" if name in obsidian_write else "obsidian_read_enabled"
            elif service == "calendar_write_service":
                setting = "google_calendar_write_enabled"
            elif service == "calendar_service":
                setting = "google_calendar_enabled"
            elif service == "vision_service":
                setting = "vision_enabled"
            elif name.startswith("vision_"):
                setting = "vision_enabled"
            elif name.startswith("safe_browser_") or name == "safe_web_search":
                setting = "safe_browser_enabled"
            elif name in {"current_media", "local_media_control"}:
                setting = "gsmtc_enabled"
            elif name.startswith("app_catalog_"):
                setting = "local_control_enabled"
            elif name.startswith("local_"):
                setting = "local_control_enabled"
            else:
                setting = "always_registered"
            enabled = True if setting == "always_registered" else bool(getattr(config, setting))
            if name.startswith("app_catalog_"):
                enabled = enabled and config.application_catalog_enabled
            if name.startswith("obsidian_"):
                enabled = enabled and bool(config.obsidian_enabled and config.obsidian_vault_path)
            tools[name] = ToolMeta(name, risk, source, enabled, setting)
        allowed = {app.name for app in windows_default_catalog().list_applications()}
        allowed.update({"spotify", "obsidian", "default_browser"})
        return cls(tools, allowed)

    def snapshot(self) -> dict:
        paths = {ROOT / "src/jarvis/__main__.py", ROOT / "src/jarvis/desktop/catalog.py",
                 ROOT / "src/jarvis/config.py",
                 ROOT / "src/jarvis/application/intent_coordinator.py",
                 ROOT / "src/jarvis/application/semantic_read_dispatcher.py",
                 ROOT / "src/jarvis/application/local_control_dispatcher.py",
                 ROOT / "src/jarvis/harness.py",
                 ROOT / "src/jarvis/tools/registry.py",
                 ROOT / "src/jarvis/local_control/backend.py",
                 ROOT / "src/jarvis/local_control/models.py",
                 ROOT / "src/jarvis/local_control/service.py",
                 ROOT / "src/jarvis/local_control/windows.py"}
        paths.update(ROOT / ('src/jarvis/' + path) for path in (
            'desktop/application_catalog.py', 'desktop/application_discovery.py',
            'desktop/application_windows.py', 'desktop/application_control.py',
            'application/application_dispatcher.py', 'security/permissions.py',
            'organization/service.py', 'organization/windows.py',
            'application/organization_routing.py', 'security/filesystem.py'))
        paths.update(ROOT / tool.source for tool in self.tools.values())
        return {
            "tools": [asdict(tool) for tool in sorted(self.tools.values(), key=lambda x: x.name)],
            "allowed_desktop_applications": sorted(self.applications),
            "source_sha256": {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                              for path in sorted(paths)},
        }


def resolve(text: str, catalog: Catalog) -> Resolution:
    started = time.perf_counter_ns()
    meaning = parse(text)
    outcome, reason, tool = "reject", "unrecognized_or_unsupported", None
    if meaning.negated:
        reason = "explicit_negation_no_external_action"
    elif meaning.ambiguous or meaning.intent is None or meaning.action is None:
        reason = "uncertain_action_or_target"
    elif (meaning.intent == "datetime" and catalog.active("get_datetime")
          and catalog.tools["get_datetime"].risk in ("SAFE", "READ_ONLY")):
        outcome, reason, tool = "execute", "registered_read_tool_exact_intent", "get_datetime"
    elif meaning.intent == "application" and meaning.action == "open":
        if catalog.active("desktop_open_application") and meaning.target in catalog.applications:
            outcome, reason, tool = "confirm", "external_effect_requires_confirmation", "desktop_open_application"
        else:
            reason = "tool_disabled_or_target_not_allowlisted"
    elif meaning.intent == "application" and meaning.action == "close":
        if catalog.active("desktop_close_window") and meaning.target in catalog.applications:
            outcome, reason, tool = "confirm", "window_binding_required", "desktop_close_window"
        else:
            reason = "tool_disabled_or_window_unbound"
    else:
        reason = "no_registered_tool_for_intent"
    elapsed = (time.perf_counter_ns() - started) / 1_000_000
    return Resolution(outcome, reason, tool, meaning, elapsed)


if __name__ == "__main__":
    print(json.dumps(Catalog.from_checkout().snapshot(), ensure_ascii=False, indent=2))
