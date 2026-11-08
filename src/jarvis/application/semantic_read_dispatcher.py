"""Fail-closed semantic dispatch for a tiny local read-only allowlist."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
import threading
import time
from uuid import uuid4

from jarvis.application.read_command_normalizer import ReadCommandNormalizer
from jarvis.runtime.fast_path import FastIntent, format_datetime_tool_result
from jarvis.security.risk import RiskLevel
from jarvis.tools.execution_result import ToolExecutionResult
from jarvis.tools.registry import ToolRegistry
from jarvis.voice.semantic_shadow import load_catalog


_ALLOWLIST = frozenset({"get_datetime", "get_runtime_info"})
_DOMAIN_TOKENS = frozenset({
    "hora", "horas", "data", "dia", "semana", "runtime", "timeout",
    "datetime",
})


@dataclass(frozen=True, slots=True)
class SemanticReadDecision:
    handled: bool
    executed: bool
    reason: str
    response: str | None = None
    tool_name: str | None = None
    interaction_id: str | None = None


@dataclass(frozen=True, slots=True)
class _ResolvedIntent:
    recognized: bool
    tool_name: str | None = None
    action: str | None = None
    arguments: dict[str, object] | None = None
    reason: str = "unrecognized"


ExecuteTool = Callable[[object, dict[str, object], object | None, str | None], ToolExecutionResult]
Resolver = Callable[[str], _ResolvedIntent]


class SemanticReadDispatcher:
    """Resolve and execute only catalog-proven local read-only Tools once."""

    def __init__(
        self,
        registry: ToolRegistry,
        execute_tool: ExecuteTool,
        *,
        catalog: dict | None = None,
        timeout_seconds: float = 0.25,
        clock: Callable[[], float] = time.monotonic,
        resolver: Resolver | None = None,
        max_history: int = 256,
    ) -> None:
        if timeout_seconds <= 0 or max_history <= 0:
            raise ValueError("Limites do dispatcher semântico devem ser positivos.")
        self._registry = registry
        self._execute_tool = execute_tool
        self._clock = clock
        self._resolver = resolver or self._resolve
        self._timeout_seconds = timeout_seconds
        self._max_history = max_history
        self._instance_id = uuid4().hex
        self._lock = threading.Lock()
        self._history: OrderedDict[str, SemanticReadDecision] = OrderedDict()
        try:
            self._catalog = catalog if catalog is not None else load_catalog()
            self._normalizer = ReadCommandNormalizer(
                self._catalog.get("read_command_contract", {}),
                tool_names=set(_ALLOWLIST),
            )
            self._catalog_error = None
        except (OSError, UnicodeError, ValueError, AttributeError):
            self._catalog = None
            self._normalizer = None
            self._catalog_error = "catalog_unavailable"

    @property
    def eligible_tools(self) -> tuple[str, ...]:
        return tuple(name for name in sorted(_ALLOWLIST) if self._eligible(name))

    def new_interaction_id(self) -> str:
        return f"{self._instance_id}:{uuid4().hex}"

    def dispatch(
        self,
        text: str,
        *,
        interaction_id: str | None = None,
        requested_tool: str | None = None,
        claimed_risk: str | None = None,
        arguments: dict[str, object] | None = None,
    ) -> SemanticReadDecision:
        correlation = interaction_id or self.new_interaction_id()
        with self._lock:
            previous = self._history.get(correlation)
            if previous is not None:
                return previous
            if not correlation.startswith(f"{self._instance_id}:"):
                return self._remember(correlation, self._reject(
                    "stale_interaction", correlation, handled=True,
                ))
            self._history[correlation] = self._reject(
                "interaction_in_progress", correlation, handled=True,
            )

        started = self._clock()
        try:
            resolution = self._resolver(text)
        except Exception:
            return self._finish(correlation, self._reject(
                "normalization_error", correlation, handled=True,
            ))
        if self._clock() - started > self._timeout_seconds:
            return self._finish(correlation, self._reject(
                "resolution_timeout", correlation, handled=resolution.recognized,
            ))
        if not resolution.recognized:
            return self._finish(correlation, self._reject(
                resolution.reason, correlation, handled=False,
            ))
        if requested_tool is not None or claimed_risk is not None:
            return self._finish(correlation, self._reject(
                "authority_override_rejected", correlation, handled=True,
            ))
        if arguments is not None and arguments:
            return self._finish(correlation, self._reject(
                "invalid_arguments", correlation, handled=True,
            ))
        tool_name = resolution.tool_name
        if tool_name not in _ALLOWLIST or not self._eligible(tool_name):
            return self._finish(correlation, self._reject(
                "capability_unavailable", correlation, handled=True,
            ))
        tool = self._registry.get(tool_name)
        if tool is None or tool.risk is not RiskLevel.READ_ONLY:
            return self._finish(correlation, self._reject(
                "tool_unavailable_or_risk_mismatch", correlation, handled=True,
            ))
        call_arguments = dict(resolution.arguments or {})
        if call_arguments:
            return self._finish(correlation, self._reject(
                "invalid_arguments", correlation, handled=True,
            ))
        if self._clock() - started > self._timeout_seconds:
            return self._finish(correlation, self._reject(
                "resolution_timeout", correlation, handled=True,
            ))
        try:
            result = self._execute_tool(tool, call_arguments, None, correlation)
        except Exception:
            return self._finish(correlation, self._reject(
                "execution_error", correlation, handled=True,
            ))
        if result.status != "success":
            return self._finish(correlation, self._reject(
                "execution_rejected", correlation, handled=True,
            ))
        response = self._format_response(result.content, resolution.action)
        return self._finish(correlation, SemanticReadDecision(
            True, True, "executed_once", response, tool_name, correlation,
        ))

    def _eligible(self, tool_name: str) -> bool:
        if self._catalog is None or tool_name not in _ALLOWLIST:
            return False
        rows = {row.get("id"): row for row in self._catalog.get("capabilities", [])}
        row = rows.get(tool_name)
        tool = self._registry.get(tool_name)
        if not isinstance(row, dict) or tool is None:
            return False
        return bool(
            row.get("tool") == tool_name
            and row.get("risk") == "read_only"
            and row.get("source_risk") == "READ_ONLY"
            and row.get("availability") == "enabled_by_config"
            and row.get("implementation_proven") is True
            and row.get("readiness_proven") is True
            and row.get("state") == "implemented_tested"
            and row.get("side_effects") is False
            and row.get("network") is False
            and row.get("gui") is False
            and bool(row.get("tests"))
            and row.get("parameters") == tool.parameters
            and tool.risk is RiskLevel.READ_ONLY
        )

    def _resolve(self, text: str) -> _ResolvedIntent:
        if self._normalizer is None:
            return _ResolvedIntent(False, reason="catalog_unavailable")
        result = self._normalizer.normalize(text)
        if result.rejection_reason is not None:
            tokens = set(result.preserved_tokens)
            handled = bool(tokens & _DOMAIN_TOKENS) or {
                "game", "mode",
            }.issubset(tokens) or (
                len(result.preserved_tokens) <= 4
                and result.preserved_tokens[:1] == ("que",)
                and result.preserved_tokens[-1:] == ("sao",)
            )
            return _ResolvedIntent(handled, reason=result.rejection_reason)
        return _ResolvedIntent(
            True, result.tool_name, result.action, {}, "resolved",
        )

    @staticmethod
    def _format_response(content: str, action: str | None) -> str:
        if action == "time":
            return format_datetime_tool_result(content, FastIntent.TIME)
        if action == "date":
            return format_datetime_tool_result(content, FastIntent.DATE)
        if action == "weekday":
            values = dict(
                line.partition(":")[::2] for line in content.splitlines() if ":" in line
            )
            try:
                from datetime import datetime
                current = datetime.strptime(values["Data"].strip(), "%d/%m/%Y")
            except (KeyError, ValueError):
                return content
            names = ("segunda-feira", "terça-feira", "quarta-feira",
                     "quinta-feira", "sexta-feira", "sábado", "domingo")
            return f"Hoje é {names[current.weekday()]} .".replace(" .", ".")
        return content

    def _finish(self, key: str, decision: SemanticReadDecision) -> SemanticReadDecision:
        with self._lock:
            return self._remember(key, decision)

    def _remember(self, key: str, decision: SemanticReadDecision) -> SemanticReadDecision:
        self._history[key] = decision
        self._history.move_to_end(key)
        while len(self._history) > self._max_history:
            self._history.popitem(last=False)
        return decision

    @staticmethod
    def _reject(reason: str, interaction_id: str, *, handled: bool) -> SemanticReadDecision:
        response = "Não executei nenhuma Tool porque o pedido não passou pela validação."
        return SemanticReadDecision(handled, False, reason, response if handled else None,
                                    interaction_id=interaction_id)
