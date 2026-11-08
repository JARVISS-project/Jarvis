"""Strict local Calendar interpretation; no model or provider calls."""
from __future__ import annotations

import re

from jarvis.runtime.fast_path import FastPathRouter


def looks_like_calendar_read(message: str) -> bool:
    text = FastPathRouter.normalize(message)
    return bool(re.search(
        r"\b(?:agenda|compromissos?|calendario|eventos?|reuniao|reunioes)\b|"
        r"\bo que (?:eu )?tenho\b|\btenho algo (?:hoje|amanha)\b", text
    )) and not bool(re.match(r"^(?:jarvis )?(?:abra|abrir|crie|criar|altere|exclua)\b", text))


def read_request(message: str) -> tuple[str, str] | None:
    text = FastPathRouter.normalize(message)
    alias = "principal"
    selected = re.fullmatch(
        r"(.+) no calend[aá]rio ([a-z][a-z0-9_]{0,31})[?.!]?", message.casefold().strip()
    )
    if selected:
        text, alias = selected.groups()
    match = FastPathRouter().match(text)
    if match is None or match.period not in {"today", "tomorrow", "upcoming"}:
        return None
    return match.period, alias
