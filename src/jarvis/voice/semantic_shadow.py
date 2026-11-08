"""Read-only semantic observation for accepted voice transcripts."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from difflib import get_close_matches
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import re
import time
import sys
import unicodedata
from uuid import uuid4


_ROOT = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[3]))
DEFAULT_CATALOG_PATH = _ROOT / "docs/sprint-23ad-capability-catalog.json"
DEFAULT_LOG_PATH = (Path.cwd() if getattr(sys, 'frozen', False) else _ROOT) / "data/voice/semantic_shadow.jsonl"
_VALID_RISKS = {
    "read_only", "reversible_local", "external_effect", "sensitive", "destructive"
}
_VERBS = {
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
_NUMBERS = {"zero": 0, "um": 1, "uma": 1, "cinco": 5, "quinze": 15,
            "cinquenta": 50}
_NEGATIONS = {"não", "nao", "nunca", "jamais"}
_SENSITIVE = re.compile(
    r"(?ix)(?:"
    r"\b(?:senha|password|token|segredo|secret|cpf|cart[aã]o)\b\s*[:=]?\s*\S+|"
    r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b|"
    r"\b(?:https?://|www\.)\S+|"
    r"(?:[A-Z]:\\|/home/|/users/)\S+|"
    r"\b(?:\d[ -]?){6,}\d\b)"
)


@dataclass(frozen=True)
class ShadowMeaning:
    intent: str | None = None
    action: str | None = None
    target: str | None = None
    parameters: dict[str, object] = field(default_factory=dict)
    negated: bool = False
    ambiguous: bool = False
    phonetic_hints: tuple[str, ...] = ()


@dataclass(frozen=True)
class ShadowResolution:
    decision: str
    reason: str
    candidate: str | None
    risk: str
    meaning: ShadowMeaning


def normalize_text(text: str) -> str:
    value = unicodedata.normalize("NFKC", text).casefold()
    value = re.sub(r"[^\w\sÀ-ÿ]", " ", value)
    value = " ".join(value.split())
    return re.sub(r"(?:\s+por favor)$", "", value)


def sanitize_text(text: str) -> str:
    redacted = _SENSITIVE.sub(" zzshadowredactedzz ", text)
    normalized = normalize_text(redacted)
    return normalized.replace("zzshadowredactedzz", "[redacted]")[:500]


def _number(value: str) -> int | None:
    return int(value) if value.isdecimal() else _NUMBERS.get(value)


def parse_text(text: str) -> ShadowMeaning:
    value = normalize_text(text)
    words = value.split()
    negated = any(word in _NEGATIONS for word in words)
    clean = " ".join(word for word in words if word not in _NEGATIONS)
    if not clean:
        return ShadowMeaning(negated=negated)
    if re.fullmatch(r"(?:que|qual) horas? (?:são|sao|é|e)", clean):
        return ShadowMeaning("datetime", "read_time", "local_clock",
                             {"kind": "time"}, negated)
    if re.fullmatch(r"qual (?:é |e )?a data de hoje", clean):
        return ShadowMeaning("datetime", "read_date", "local_clock",
                             {"kind": "date"}, negated)
    match = re.fullmatch(r"quem (?:é|e) o autor de (.+)", clean)
    if match:
        return ShadowMeaning("knowledge", "ask_author", match.group(1),
                             negated=negated)
    match = re.fullmatch(r"qual a (?:diferença|diferenca) entre (.+?) e (.+)", clean)
    if match:
        left, right = match.groups()
        return ShadowMeaning("comparison", "compare", "terms",
                             {"left": left, "right": right}, negated,
                             ambiguous=left == right)
    first, _, rest = clean.partition(" ")
    operation = _VERBS.get(first)
    if operation is None:
        hints = tuple(get_close_matches(first, _VERBS, n=3, cutoff=0.72))
        return ShadowMeaning(negated=negated, ambiguous=bool(hints),
                             phonetic_hints=hints)
    intent, action = operation
    rest = re.sub(r"^(?:o|a|os|as)\s+", "", rest)
    if intent == "application":
        return ShadowMeaning(intent, action, rest or None, negated=negated,
                             ambiguous=not bool(rest))
    if intent == "volume":
        target = "volume" if rest == "volume" else (rest or None)
        return ShadowMeaning(intent, action, target, negated=negated,
                             ambiguous=target != "volume")
    if intent == "media":
        targets = {"pause": ({"música", "musica"}, "music"),
                   "skip": ({"para a próxima faixa", "para a proxima faixa",
                              "próxima faixa", "proxima faixa"}, "next_track")}
        accepted, canonical = targets.get(action, (set(), None))
        target = canonical if rest in accepted else (rest or None)
        return ShadowMeaning(intent, action, target, negated=negated,
                             ambiguous=canonical is None or target != canonical)
    if intent == "automation":
        match = re.fullmatch(r"luzes da (.+)", rest)
        return ShadowMeaning(intent, action,
                             f"lights:{match.group(1)}" if match else (rest or None),
                             negated=negated, ambiguous=match is None)
    if intent == "timer":
        match = re.fullmatch(r"um timer de (\w+) minutos?", rest)
        number = _number(match.group(1)) if match else None
        return ShadowMeaning(intent, action, "timer" if match else (rest or None),
                             {"minutes": number} if number is not None else {},
                             negated, ambiguous=number is None)
    match = re.fullmatch(r"(.+?) (?:à|a) lista de compras", rest)
    return ShadowMeaning(intent, action, "shopping_list" if match else (rest or None),
                         {"item": match.group(1)} if match else {}, negated,
                         ambiguous=match is None)


def load_catalog(path: Path = DEFAULT_CATALOG_PATH) -> dict:
    raw = path.read_bytes()
    data = json.loads(raw.decode("utf-8"))
    if data.get("schema_version") != 1 or not isinstance(data.get("capabilities"), list):
        raise ValueError("invalid catalog schema")
    rows = data["capabilities"]
    identifiers = [row.get("id") for row in rows]
    if not identifiers or len(identifiers) != len(set(identifiers)):
        raise ValueError("invalid catalog capabilities")
    for row in rows:
        if row.get("risk") not in _VALID_RISKS or not row.get("actions"):
            raise ValueError("invalid catalog risk or action")
        if row.get("availability") not in {"enabled_by_config", "disabled_by_config"}:
            raise ValueError("invalid catalog availability")
    for relative, expected in data.get("source_sha256", {}).items():
        source = _ROOT / relative
        if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != expected:
            raise ValueError("catalog source drift")
    data["_catalog_version"] = f"sha256:{hashlib.sha256(raw).hexdigest()}"
    return data


def resolve_text(text: str, catalog: dict) -> ShadowResolution:
    meaning = parse_text(text)
    rows = {row["id"]: row for row in catalog["capabilities"]}
    decision, reason, candidate, risk = "reject", "unrecognized_or_unsupported", None, "unknown"
    if meaning.negated:
        reason = "explicit_negation"
    elif meaning.ambiguous or not meaning.intent or not meaning.action or not meaning.target:
        reason = "uncertain_action_or_target"
    elif meaning.intent == "datetime":
        row = rows.get("get_datetime")
        if row and row["availability"] == "enabled_by_config" and row["risk"] == "read_only":
            decision, reason, candidate, risk = (
                "execute", "registered_read_capability", row["id"], row["risk"])
        else:
            reason = "capability_unavailable_or_risk_unknown"
    elif meaning.intent == "application" and meaning.action in {"open", "close"}:
        capability = ("desktop_open_application" if meaning.action == "open"
                      else "desktop_close_window")
        row = rows.get(capability)
        if row:
            risk = row.get("risk", "unknown")
            aliases = row.get("targets", {}).get("aliases", {})
            target = aliases.get(meaning.target, meaning.target)
            allowed = row.get("targets", {}).get("values", [])
            if row["availability"] == "enabled_by_config" and target in allowed and risk in _VALID_RISKS:
                decision, reason, candidate = "confirm", "effect_requires_confirmation", capability
            else:
                reason = "capability_disabled_or_target_not_allowlisted"
    else:
        reason = "no_registered_capability_for_intent"
    if risk not in _VALID_RISKS:
        return ShadowResolution("reject", "unknown_risk", None, "unknown", meaning)
    return ShadowResolution(decision, reason, candidate, risk, meaning)


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(record.msg, ensure_ascii=False, separators=(",", ":"))


class SemanticShadowObserver:
    """Observe a transcript without holding any Tool or confirmation dependency."""

    def __init__(self, *, catalog_path: Path = DEFAULT_CATALOG_PATH,
                 log_path: Path = DEFAULT_LOG_PATH, max_bytes: int = 250_000,
                 backup_count: int = 2,
                 clock: object = time.perf_counter_ns) -> None:
        self._catalog_path = catalog_path
        self._catalog: dict | None = None
        self._catalog_error: str | None = None
        self._clock = clock
        try:
            self._catalog = load_catalog(catalog_path)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
            self._catalog_error = type(error).__name__
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self._logger = logging.getLogger(f"jarvis.voice.semantic_shadow.{id(self)}")
        self._logger.setLevel(logging.INFO)
        self._logger.propagate = False
        handler = RotatingFileHandler(log_path, maxBytes=max_bytes,
                                      backupCount=backup_count, encoding="utf-8")
        handler.setFormatter(_JsonFormatter())
        self._logger.addHandler(handler)

    def observe(self, text: str, *, activation_origin: str,
                interaction_id: str | None = None) -> ShadowResolution:
        started = self._clock()
        if self._catalog is None:
            resolution = ShadowResolution("reject", "catalog_unavailable", None,
                                          "unknown", parse_text(text))
        else:
            resolution = resolve_text(text, self._catalog)
        elapsed_ms = (self._clock() - started) / 1_000_000
        payload = {
            "interaction_id": interaction_id or str(uuid4()),
            "activation_origin": activation_origin,
            "normalized_text": sanitize_text(text),
            "candidate": resolution.candidate,
            "decision": resolution.decision,
            "reason": resolution.reason,
            "duration_ms": round(elapsed_ms, 4),
            "catalog_version": self._catalog.get("_catalog_version") if self._catalog else None,
            "error": self._catalog_error,
        }
        self._logger.info(payload)
        return resolution

    def close(self) -> None:
        for handler in tuple(self._logger.handlers):
            self._logger.removeHandler(handler)
            handler.close()
