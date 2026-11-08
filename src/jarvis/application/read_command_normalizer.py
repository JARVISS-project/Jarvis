"""Deterministic, fail-closed normalization for read-only voice commands."""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata


_MAX_TEXT_LENGTH = 500
_NEGATIONS = frozenset({"nao", "nunca", "jamais", "sem", "nem"})
_SUSPICIOUS_MOJIBAKE = (
    "\ufffd", "Ã£", "Ã©", "Ãª", "Ã¡", "Ã³", "Ãº", "Ã§", "Â·", "Â ",
)


@dataclass(frozen=True, slots=True)
class NormalizedReadCommand:
    sanitized_original: str
    normalized_text: str
    transformations: tuple[str, ...]
    preserved_tokens: tuple[str, ...]
    has_negation: bool
    rejection_reason: str | None = None
    tool_name: str | None = None
    action: str | None = None


class ReadCommandNormalizer:
    """Match only explicit aliases from a versioned capability contract."""

    def __init__(self, contract: dict, *, tool_names: set[str]) -> None:
        self._aliases: dict[str, tuple[str, str]] = {}
        self._prefixes: tuple[tuple[str, ...], ...] = ()
        self._suffixes: tuple[tuple[str, ...], ...] = ()
        self._validate_and_compile(contract, tool_names)

    def normalize(self, text: str) -> NormalizedReadCommand:
        sanitized = self._sanitize(text)
        if not sanitized:
            return self._result(sanitized, "", (), (), False, "empty_transcript")
        if len(sanitized) > _MAX_TEXT_LENGTH:
            return self._result(sanitized, "", (), (), False, "text_too_long")
        if any(marker in sanitized for marker in _SUSPICIOUS_MOJIBAKE):
            return self._result(sanitized, "", (), (), False, "invalid_unicode")

        transformations: list[str] = []
        value = unicodedata.normalize("NFC", sanitized)
        if value != sanitized:
            transformations.append("unicode_nfc")
        folded = value.casefold()
        if folded != value:
            transformations.append("casefold")
        unaccented = self._unaccent(folded)
        if unaccented != folded:
            transformations.append("remove_diacritics")
        punctuated = re.sub(r"[^a-z0-9]+", " ", unaccented)
        if punctuated.strip() != unaccented.strip():
            transformations.append("punctuation")
        normalized = " ".join(punctuated.split())
        if normalized != punctuated.strip():
            transformations.append("whitespace")
        tokens = tuple(normalized.split())
        has_negation = any(token in _NEGATIONS for token in tokens)
        if has_negation:
            return self._result(sanitized, normalized, transformations, tokens, True,
                                "explicit_negation")

        core = tokens
        for prefix in self._prefixes:
            if core[:len(prefix)] == prefix:
                core = core[len(prefix):]
                transformations.append("leading_marker")
                break
        for suffix in self._suffixes:
            if len(core) >= len(suffix) and core[-len(suffix):] == suffix:
                core = core[:-len(suffix)]
                transformations.append("politeness")
                break
        normalized_core = " ".join(core)
        match = self._aliases.get(normalized_core)
        if match is None:
            reason = "incomplete_or_residual_text" if core else "incomplete_command"
            return self._result(sanitized, normalized_core, transformations, tokens,
                                False, reason)
        tool_name, action = match
        return self._result(sanitized, normalized_core, transformations, tokens,
                            False, None, tool_name, action)

    def _validate_and_compile(self, contract: dict, tool_names: set[str]) -> None:
        if contract.get("version") != 1:
            raise ValueError("invalid read command contract version")
        prefixes = self._phrases(contract.get("leading_markers"), "leading markers")
        suffixes = self._phrases(contract.get("politeness"), "politeness")
        removable = set(prefixes) | set(suffixes)
        commands = contract.get("commands")
        if not isinstance(commands, list) or not commands:
            raise ValueError("missing read command contract")
        for command in commands:
            tool_name = command.get("capability")
            action = command.get("action")
            aliases = command.get("aliases")
            if tool_name not in tool_names or not isinstance(action, str):
                raise ValueError("invalid read command capability")
            if not isinstance(aliases, list) or not aliases:
                raise ValueError("incomplete read command alias")
            for alias in aliases:
                canonical = self._canonical_contract_phrase(alias)
                tokens = tuple(canonical.split())
                if not tokens or tokens in removable:
                    raise ValueError("incomplete read command alias")
                if any(token in _NEGATIONS for token in tokens):
                    raise ValueError("negated read command alias")
                if canonical in {self._canonical_contract_phrase(name)
                                 for name in tool_names}:
                    raise ValueError("read command alias is a Tool name")
                if any(tokens[:len(marker)] == marker for marker in prefixes):
                    raise ValueError("removable marker required by command")
                previous = self._aliases.get(canonical)
                current = (tool_name, action)
                if previous is not None and previous != current:
                    raise ValueError("normalized read command collision")
                self._aliases[canonical] = current
        self._prefixes = tuple(sorted(prefixes, key=len, reverse=True))
        self._suffixes = tuple(sorted(suffixes, key=len, reverse=True))

    @classmethod
    def _phrases(cls, value: object, label: str) -> tuple[tuple[str, ...], ...]:
        if not isinstance(value, list):
            raise ValueError(f"invalid {label}")
        phrases = tuple(tuple(cls._canonical_contract_phrase(item).split())
                        for item in value if isinstance(item, str))
        if len(phrases) != len(value) or any(not phrase for phrase in phrases):
            raise ValueError(f"invalid {label}")
        return phrases

    @classmethod
    def _canonical_contract_phrase(cls, value: str) -> str:
        folded = cls._unaccent(unicodedata.normalize("NFC", value).casefold())
        return " ".join(re.sub(r"[^a-z0-9]+", " ", folded).split())

    @staticmethod
    def _unaccent(value: str) -> str:
        decomposed = unicodedata.normalize("NFD", value)
        return "".join(char for char in decomposed if not unicodedata.combining(char))

    @staticmethod
    def _sanitize(text: str) -> str:
        if not isinstance(text, str):
            return ""
        without_controls = "".join(
            " " if unicodedata.category(char).startswith("C") else char for char in text
        )
        return " ".join(without_controls.split())

    @staticmethod
    def _result(sanitized: str, normalized: str, transformations: list[str] | tuple[str, ...],
                tokens: tuple[str, ...], negated: bool, reason: str | None,
                tool_name: str | None = None, action: str | None = None,
                ) -> NormalizedReadCommand:
        return NormalizedReadCommand(
            sanitized, normalized, tuple(dict.fromkeys(transformations)), tokens,
            negated, reason, tool_name, action,
        )
