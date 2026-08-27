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

_TECH_INITIALISMS = ("STT", "TTS", "LLM", "GPU", "CPU", "API")
_STATUS_REPLACEMENTS = (
    ("NOT CONFIGURED", "não configurado"),
    ("NOT INSTALLED", "não instalado"),
    ("DISABLED", "desabilitado"),
    ("ENABLED", "habilitado"),
    ("INSTALLED", "instalado"),
    ("YES", "sim"),
    ("NO", "não"),
    ("OFF", "desligado"),
    ("ON", "ligado"),
    ("OK", "ok"),
)


def _expand_pt_br_clock(match: re.Match[str]) -> str:
    hours = int(match.group(1))
    minutes = int(match.group(2))
    hour_word = "hora" if hours == 1 else "horas"
    if minutes == 0:
        return f"{hours} {hour_word}"
    minute_word = "minuto" if minutes == 1 else "minutos"
    return f"{hours} {hour_word} e {minutes} {minute_word}"


def _prepare_technical_speech(text: str) -> str:
    """Make terminal-oriented labels less awkward for pt-BR TTS.

    This only affects the audio copy. Terminal/UI output remains byte-for-byte
    unchanged, so useful technical spelling is not lost from logs.
    """

    prepared = re.sub(r"\.{4,}", ": ", text)
    prepared = re.sub(r"\.md\b", "", prepared, flags=re.IGNORECASE)
    for source, target in _STATUS_REPLACEMENTS:
        prepared = re.sub(rf"\b{re.escape(source)}\b", target, prepared)
    for initialism in _TECH_INITIALISMS:
        prepared = re.sub(
            rf"\b{initialism}\b",
            " ".join(initialism),
            prepared,
        )
    return prepared


def prepare_text_for_speech(text: str) -> str:
    """Prepare terminal/Markdown output for natural spoken pt-BR."""
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
    prepared = _prepare_technical_speech(prepared)
    prepared = re.sub(r"[ \t]+", " ", prepared)
    # Lines and bullets are visual structure. For speech, finish bare lines as
    # sentences so Piper can create a real breathing pause between ideas.
    lines = [line.strip() for line in prepared.splitlines() if line.strip()]
    if len(lines) > 1:
        spoken_lines: list[str] = []
        for line in lines:
            if line[-1:] not in ".!?;:":
                line += "."
            spoken_lines.append(line)
        prepared = " ".join(spoken_lines)
    elif lines:
        prepared = lines[0]
    return prepared.strip()
