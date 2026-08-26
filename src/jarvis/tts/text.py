"""Small, conservative cleanup for text that will be spoken."""

import re


_EMOJI_PATTERN = re.compile(
    "["
    "\U0001F1E6-\U0001F1FF"
    "\U0001F300-\U0001FAFF"
    "\u2600-\u26FF"
    "\u2700-\u27BF"
    "\uFE0F"
    "\u200D"
    "]+"
)


def _expand_pt_br_clock(match: re.Match[str]) -> str:
    hours = int(match.group(1))
    minutes = int(match.group(2))
    hour_word = "hora" if hours == 1 else "horas"
    if minutes == 0:
        return f"{hours} {hour_word}"
    minute_word = "minuto" if minutes == 1 else "minutos"
    return f"{hours} {hour_word} e {minutes} {minute_word}"


def prepare_text_for_speech(text: str) -> str:
    """Remove basic visual Markdown without changing terminal output."""
    prepared = text.strip()
    if not prepared:
        return ""

    prepared = re.sub(r"(?m)^\s{0,3}#{1,6}\s+", "", prepared)
    prepared = re.sub(r"(?m)^\s*[-*+]\s+", "", prepared)
    prepared = re.sub(r"\[([^]]+)]\([^)]+\)", r"\1", prepared)
    prepared = re.sub(r"[*_`]+", "", prepared)
    prepared = _EMOJI_PATTERN.sub("", prepared)
    prepared = re.sub(
        r"\b([01]?\d|2[0-3])h([0-5]\d)\b",
        _expand_pt_br_clock,
        prepared,
    )
    prepared = re.sub(r"[ \t]+", " ", prepared)
    prepared = re.sub(r"\n{2,}", "\n", prepared)
    return prepared.strip()
