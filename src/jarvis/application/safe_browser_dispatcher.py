from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
import re
import threading
import unicodedata
from uuid import uuid4

from jarvis.security.risk import RiskLevel
from jarvis.safe_browser.policy import UrlPolicy
from jarvis.tools.execution_result import ToolExecutionResult
from jarvis.tools.registry import ToolRegistry
from jarvis.voice.semantic_shadow import load_catalog


_EXACT = {
    "abra o navegador": ("safe_browser_open", {"destination": "browser_home"}),
    "abra o google": ("safe_browser_open", {"destination": "google"}),
    "abra o youtube": ("safe_browser_open", {"destination": "youtube"}),
    "abra o github": ("safe_browser_open", {"destination": "github"}),
    "abra o google agenda": ("safe_browser_open", {"destination": "google_calendar"}),
    "abra o google drive": ("safe_browser_open", {"destination": "google_drive"}),
    "abra o google docs": ("safe_browser_open", {"destination": "google_docs"}),
    "abra o whatsapp web": ("safe_browser_open", {"destination": "whatsapp_web"}),
    "abra o spotify web": ("safe_browser_open", {"destination": "spotify_web"}),
    "liste os destinos disponiveis": ("safe_browser_destinations", {}),
    "quais destinos estao disponiveis": ("safe_browser_destinations", {}),
    "existe navegador padrao": ("safe_browser_status", {}),
    "o navegador esta disponivel": ("safe_browser_status", {}),
}
_SEARCH_ORIGINAL = re.compile(
    r"^\s*pesquise\s+(?:na\s+web|no\s+google)\s+por\s+(.+?)\s*[.!?]?\s*$",
    re.IGNORECASE,
)
_NEGATIONS = frozenset({"nao", "nunca", "jamais", "sem", "nem"})
_RISKS = {
    "safe_browser_status": RiskLevel.READ_ONLY,
    "safe_browser_destinations": RiskLevel.READ_ONLY,
    "safe_browser_open": RiskLevel.REVERSIBLE_LOCAL,
    "safe_web_search": RiskLevel.EXTERNAL_EFFECT,
}


@dataclass(frozen=True, slots=True)
class SafeBrowserDecision:
    handled: bool
    executed: bool
    reason: str
    response: str | None = None
    interaction_id: str | None = None


ExecuteTool = Callable[[object, dict[str, object], object | None, str | None], ToolExecutionResult]


class SafeBrowserDispatcher:
    def __init__(self, registry: ToolRegistry, execute_tool: ExecuteTool,
                 *, catalog: dict | None = None, max_history: int = 256) -> None:
        self._registry = registry
        self._execute_tool = execute_tool
        self._catalog = catalog if catalog is not None else load_catalog()
        self._instance_id = uuid4().hex
        self._history: OrderedDict[str, SafeBrowserDecision] = OrderedDict()
        self._max_history = max_history
        self._lock = threading.RLock()

    def new_interaction_id(self) -> str:
        return f"{self._instance_id}:{uuid4().hex}"

    def dispatch(self, text: str, *, interaction_id: str | None = None) -> SafeBrowserDecision:
        correlation = interaction_id or self.new_interaction_id()
        with self._lock:
            if correlation in self._history:
                return self._history[correlation]
            if not correlation.startswith(f"{self._instance_id}:"):
                return self._remember(correlation, SafeBrowserDecision(
                    True, False, "stale_interaction", "Interação expirada.", correlation,
                ))
            normalized = self._normalize(text)
            tokens = normalized.split()
            token_set = set(tokens)
            domain = bool(token_set & {
                "navegador", "google", "youtube", "github", "whatsapp",
                "destinos", "pesquise", "url", "http", "https",
                "safe", "browser", "search",
            }) or {"spotify", "web"}.issubset(token_set)
            if any(token in _NEGATIONS for token in tokens):
                return self._blocked(correlation, "explicit_negation", domain)
            exact = _EXACT.get(normalized)
            search = _SEARCH_ORIGINAL.fullmatch(text)
            if exact is not None:
                tool_name, arguments = exact
            elif search is not None:
                try:
                    term = UrlPolicy().normalize_search_term(
                        search.group(1).strip().rstrip(".!?").strip()
                    )
                except ValueError:
                    return self._blocked(correlation, "invalid_search_term", True)
                tool_name, arguments = "safe_web_search", {
                    "provider": "google", "term": term,
                }
            else:
                return self._blocked(correlation, "unrecognized_or_ambiguous", domain)
            tool = self._registry.get(tool_name)
            if tool is None or tool.risk is not _RISKS[tool_name] or not self._eligible(tool_name):
                return self._remember(correlation, SafeBrowserDecision(
                    True, False, "capability_unavailable",
                    "A capacidade segura de navegador está indisponível.", correlation,
                ))
            result = self._execute_tool(tool, dict(arguments), None, correlation)
            return self._remember(correlation, SafeBrowserDecision(
                True, result.status == "success" and result.authorized,
                result.status, result.content, correlation,
            ))

    def _eligible(self, tool_name: str) -> bool:
        row = next((item for item in self._catalog.get("capabilities", [])
                    if item.get("id") == tool_name), None)
        tool = self._registry.get(tool_name)
        return bool(row and tool and row.get("tool") == tool_name
                    and row.get("risk") == tool.risk.value
                    and row.get("implementation_proven") is True
                    and row.get("readiness_proven") is True
                    and row.get("state") == "implemented_tested"
                    and row.get("parameters") == tool.parameters)

    def _blocked(self, key: str, reason: str, handled: bool) -> SafeBrowserDecision:
        return self._remember(key, SafeBrowserDecision(
            handled, False, reason,
            "Não executei a solicitação de navegador porque ela não passou pela validação."
            if handled else None, key,
        ))

    def _remember(self, key: str, value: SafeBrowserDecision) -> SafeBrowserDecision:
        self._history[key] = value
        self._history.move_to_end(key)
        while len(self._history) > self._max_history:
            self._history.popitem(last=False)
        return value

    @staticmethod
    def _normalize(text: str) -> str:
        value = unicodedata.normalize("NFD", unicodedata.normalize("NFC", text).casefold())
        value = "".join(char for char in value if not unicodedata.combining(char))
        return " ".join(re.sub(r"[^a-z0-9À-ÿ]+", " ", value).split())
