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
    prepared = re.sub(r"[ \t]+", " ", prepared)
    prepared = re.sub(r"\n{2,}", "\n", prepared)
    return prepared.strip()
