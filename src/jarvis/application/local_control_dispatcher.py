from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
import re
import threading
import unicodedata
from uuid import uuid4

from jarvis.security.risk import RiskLevel
from jarvis.tools.execution_result import ToolExecutionResult
from jarvis.tools.registry import ToolRegistry
from jarvis.voice.semantic_shadow import load_catalog


_TOOL_RISKS = {
    "app_catalog_open": RiskLevel.REVERSIBLE_LOCAL,
    "app_catalog_status": RiskLevel.READ_ONLY,
    "app_catalog_window": RiskLevel.REVERSIBLE_LOCAL,
    "app_catalog_close": RiskLevel.SENSITIVE,
    "local_application_open": RiskLevel.REVERSIBLE_LOCAL,
    "local_application_status": RiskLevel.READ_ONLY,
    "local_application_focus": RiskLevel.REVERSIBLE_LOCAL,
    "local_application_close": RiskLevel.SENSITIVE,
    "local_media_control": RiskLevel.REVERSIBLE_LOCAL,
    "current_media": RiskLevel.READ_ONLY,
    "local_volume_control": RiskLevel.REVERSIBLE_LOCAL,
}
_NEGATIONS = frozenset({"nao", "nunca", "jamais", "sem", "nem"})


@dataclass(frozen=True, slots=True)
class LocalControlIntent:
    tool_name: str
    arguments: dict[str, object]


@dataclass(frozen=True, slots=True)
class LocalControlDecision:
    handled: bool
    executed: bool
    reason: str
    response: str | None = None
    tool_name: str | None = None
    interaction_id: str | None = None


class LocalControlNormalizer:
    def __init__(self, contract: dict) -> None:
        if contract.get("version") != 1 or not isinstance(contract.get("commands"), list):
            raise ValueError("invalid local control contract")
        self._aliases: dict[str, LocalControlIntent] = {}
        for row in contract["commands"]:
            tool = row.get("tool")
            arguments = row.get("arguments")
            aliases = row.get("aliases")
            if tool not in _TOOL_RISKS or not isinstance(arguments, dict) or not aliases:
                raise ValueError("invalid local control command")
            for alias in aliases:
                normalized = self._normalize(alias)
                if not normalized or any(token in _NEGATIONS for token in normalized.split()):
                    raise ValueError("invalid local control alias")
                intent = LocalControlIntent(tool, dict(arguments))
                if normalized in self._aliases and self._aliases[normalized] != intent:
                    raise ValueError("local control alias collision")
                self._aliases[normalized] = intent

    def resolve(self, text: str) -> tuple[LocalControlIntent | None, str, bool]:
        value = self._normalize(text)
        tokens = value.split()
        token_set = set(tokens)
        media_domain = bool(token_set & {
            "musica", "faixa", "volume", "mudo", "tocando", "media",
            "pause", "pausar", "continue", "reproduza", "reproduzir",
            "proxima", "anterior",
        })
        application_domain = bool(
            token_set & {"spotify", "obsidian", "navegador"}
            and token_set & {"abra", "abrir", "feche", "fechar", "traga"}
        )
        domain = media_domain or application_domain
        if any(token in _NEGATIONS for token in tokens):
            return None, "explicit_negation", domain
        intent = self._aliases.get(value)
        if intent is None:
            return None, "unrecognized_or_ambiguous", domain
        return intent, "resolved", True

    @staticmethod
    def _normalize(text: str) -> str:
        value = unicodedata.normalize("NFD", unicodedata.normalize("NFC", text).casefold())
        value = "".join(char for char in value if not unicodedata.combining(char))
        return " ".join(re.sub(r"[^a-z0-9]+", " ", value).split())


ExecuteTool = Callable[[object, dict[str, object], object | None, str | None], ToolExecutionResult]


class LocalControlDispatcher:
    def __init__(self, registry: ToolRegistry, execute_tool: ExecuteTool,
                 *, catalog: dict | None = None, max_history: int = 256) -> None:
        self._registry = registry
        self._execute_tool = execute_tool
        self._instance_id = uuid4().hex
        self._lock = threading.RLock()
        self._history: OrderedDict[str, LocalControlDecision] = OrderedDict()
        self._max_history = max_history
        try:
            self._catalog = catalog if catalog is not None else load_catalog()
            self._normalizer = LocalControlNormalizer(
                self._catalog.get("local_control_contract", {})
            )
        except (OSError, UnicodeError, ValueError, AttributeError):
            self._catalog = None
            self._normalizer = None

    def new_interaction_id(self) -> str:
        return f"{self._instance_id}:{uuid4().hex}"

    def dispatch(self, text: str, *, interaction_id: str | None = None) -> LocalControlDecision:
        correlation = interaction_id or self.new_interaction_id()
        with self._lock:
            if correlation in self._history:
                return self._history[correlation]
            if not correlation.startswith(f"{self._instance_id}:"):
                return self._remember(correlation, LocalControlDecision(
                    True, False, "stale_interaction", "Interação expirada.", interaction_id=correlation,
                ))
            if self._normalizer is None:
                return self._remember(correlation, LocalControlDecision(
                    False, False, "catalog_unavailable", interaction_id=correlation,
                ))
            intent, reason, handled = self._normalizer.resolve(text)
            if intent is None:
                return self._remember(correlation, LocalControlDecision(
                    handled, False, reason,
                    "Não executei o controle local porque o pedido não passou pela validação."
                    if handled else None, interaction_id=correlation,
                ))
            tool = self._registry.get(intent.tool_name)
            expected_risk = _TOOL_RISKS[intent.tool_name]
            if tool is None or tool.risk is not expected_risk or not self._eligible(intent.tool_name):
                return self._remember(correlation, LocalControlDecision(
                    True, False, "capability_unavailable",
                    "A capacidade local está indisponível.", intent.tool_name, correlation,
                ))
            result = self._execute_tool(tool, dict(intent.arguments), None, correlation)
            executed = result.status == "success" and result.authorized
            return self._remember(correlation, LocalControlDecision(
                True, executed, result.status, result.content, intent.tool_name, correlation,
            ))

    def _eligible(self, tool_name: str) -> bool:
        if self._catalog is None:
            return False
        row = next((item for item in self._catalog.get("capabilities", [])
                    if item.get("id") == tool_name), None)
        tool = self._registry.get(tool_name)
        return bool(
            row and tool and row.get("tool") == tool_name
            and row.get("risk") == tool.risk.value
            and row.get("implementation_proven") is True
            and row.get("readiness_proven") is True
            and row.get("state") == "implemented_tested"
            and row.get("parameters") == tool.parameters
        )

    def _remember(self, key: str, value: LocalControlDecision) -> LocalControlDecision:
        with self._lock:
            self._history[key] = value
            self._history.move_to_end(key)
            while len(self._history) > self._max_history:
                self._history.popitem(last=False)
        return value
