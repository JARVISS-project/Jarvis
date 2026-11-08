"""Barreira fail-closed para texto final produzido pelo modelo."""

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class ValidatedFinalResponse:
    accepted: bool
    text: str = ""
    reason: str = "accepted"


class FinalResponseFirewall:
    _CLOSING_REASONING = re.compile(
        r"</(?:think|analysis|reasoning)\s*>", re.IGNORECASE
    )
    _RESPONSE_WRAPPER = re.compile(
        r"\s*<response>\s*(.*?)\s*</response>\s*",
        re.IGNORECASE | re.DOTALL,
    )
    _PROTOCOL = re.compile(
        r"</?(?:tool_call|tool_calls|analysis|reasoning)(?:\s[^>]*)?>"
        r"|\btool_call\s*:|\"tool_calls\"\s*:|\"function\"\s*:\s*\{"
        r"|\{\s*\"name\"\s*:\s*\"[^\"]+\"\s*,\s*\"arguments\"\s*:",
        re.IGNORECASE,
    )
    _REASONING_MARKERS = (
        "okay, the user asked",
        "ok, the user asked",
        "the user's question is",
        "let me check the tools",
        "looking at the tools",
        "wait, no",
        "the assistant should",
        "according to the instructions",
        "for each function call",
        "i need to respond",
        "i should ask",
    )

    def validate(self, raw_text: str | None) -> ValidatedFinalResponse:
        text = raw_text or ""
        closing = list(self._CLOSING_REASONING.finditer(text))
        if closing:
            # Alguns modelos misturam reasoning antes do fechamento e a
            # resposta final depois dele. Somente o sufixo é elegível.
            text = text[closing[-1].end():]
        elif re.search(r"<think(?:\s[^>]*)?>", text, re.IGNORECASE):
            return ValidatedFinalResponse(False, reason="reasoning_leak")

        wrapper = self._RESPONSE_WRAPPER.fullmatch(text)
        if wrapper is not None:
            text = wrapper.group(1)
        text = re.sub(
            r"</?(?:response|final)>\s*", "", text, flags=re.IGNORECASE
        ).strip()
        lowered = text.casefold()
        if self._PROTOCOL.search(text):
            return ValidatedFinalResponse(False, reason="tool_protocol")
        if any(marker in lowered for marker in self._REASONING_MARKERS):
            return ValidatedFinalResponse(False, reason="reasoning_leak")
        if not text:
            return ValidatedFinalResponse(False, reason="empty_response")
        return ValidatedFinalResponse(True, text=text)
