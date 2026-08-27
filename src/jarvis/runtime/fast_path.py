from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import re
import unicodedata


class FastIntent(Enum):
    TIME = "time"
    DATE = "date"
    DATE_TIME = "date_time"


@dataclass(frozen=True)
class FastPathMatch:
    intent: FastIntent


class FastPathRouter:
    """Reconhece apenas intenções locais, determinísticas e inequívocas."""

    # Common conversational fillers are safe to ignore because the actual
    # intent pattern still has to match the entire remaining utterance.
    _LEADING_FILLERS = r"(?:(?:e|entao|bom|beleza|olha|jarvis|agora) )*"
    _QUESTION_PREFIX = (
        r"(?:(?:(?:eu )?(?:perguntei|estou perguntando|quero saber|queria saber)|"
        r"(?:pode|consegue) me (?:dizer|falar)) )?"
    )

    _TIME_PATTERNS = (
        re.compile(
            _LEADING_FILLERS
            + _QUESTION_PREFIX
            + r"(?:(?:me )?(?:diz|diga|fala|fale)(?: ai)? )?"
            r"(?:que horas sao(?: agora)?|que hora e(?: agora)?|"
            r"qual(?: e)? a hora(?: agora)?|qual(?: e)? o horario(?: agora)?|"
            r"hora agora|horario agora|hora atual|horario atual)"
        ),
    )
    _DATE_PATTERNS = (
        re.compile(
            _LEADING_FILLERS
            + _QUESTION_PREFIX
            + r"(?:(?:me )?(?:diz|diga|fala|fale)(?: ai)? )?"
            r"(?:que dia e hoje|qual(?: e)? o dia de hoje|"
            r"qual(?: e)? a data(?: de hoje)?|data de hoje|hoje e que dia)"
        ),
    )
    _DATE_TIME_PATTERNS = (
        re.compile(
            _LEADING_FILLERS
            + _QUESTION_PREFIX
            + r"(?:(?:me )?(?:diz|diga|fala|fale)(?: ai)? )?"
            r"(?:qual(?: e)? a data e hora(?: agora)?|"
            r"que dia e que horas sao(?: agora)?|data e hora(?: agora)?)"
        ),
    )

    def match(self, message: str) -> FastPathMatch | None:
        normalized = self._repair_time_transcription(self.normalize(message))
        if any(pattern.fullmatch(normalized) for pattern in self._DATE_TIME_PATTERNS):
            return FastPathMatch(FastIntent.DATE_TIME)
        if any(pattern.fullmatch(normalized) for pattern in self._TIME_PATTERNS):
            return FastPathMatch(FastIntent.TIME)
        if any(pattern.fullmatch(normalized) for pattern in self._DATE_PATTERNS):
            return FastPathMatch(FastIntent.DATE)
        return None

    @classmethod
    def _repair_time_transcription(cls, value: str) -> str:
        """Corrige apenas erros de STT fortemente ligados a pedidos de hora.

        Não substitui ``odas`` globalmente: a palavra pode ser legítima em outro
        contexto. A correção só acontece quando a frase inteira tem a forma de
        uma pergunta de horário, evitando transformar texto comum em comando.
        """
        # "horas" can lose the initial /r/ in STT for some speakers. Keep
        # these repairs local to time-shaped utterances rather than rewriting
        # arbitrary dictated text.
        hour_like = r"(?:odas|odes)"
        value = re.sub(rf"\bque {hour_like} sao\b", "que horas sao", value)
        value = re.sub(rf"\bque {hour_like} e\b", "que hora e", value)
        value = re.sub(r"\bqual(?: e)? o odario\b", "qual e o horario", value)
        value = re.sub(r"\bque odario e\b", "qual e o horario", value)
        bare = re.fullmatch(
            cls._LEADING_FILLERS + rf"{hour_like} sao(?: agora)?",
            value,
        )
        if bare is not None:
            match = re.search(rf"{hour_like} sao", value)
            if match is not None:
                value = value[:match.start()] + "que horas sao"
        return value

    @staticmethod
    def normalize(message: str) -> str:
        value = unicodedata.normalize("NFKD", message.casefold())
        value = "".join(char for char in value if not unicodedata.combining(char))
        value = re.sub(r"[^a-z0-9]+", " ", value)
        return " ".join(value.split())


_MONTHS = (
    "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
)


def format_datetime_tool_result(content: str, intent: FastIntent) -> str:
    values: dict[str, str] = {}
    for line in content.splitlines():
        key, separator, value = line.partition(":")
        if separator:
            values[key.strip().casefold()] = value.strip()

    try:
        current = datetime.strptime(
            f"{values['data']} {values['hora']}", "%d/%m/%Y %H:%M"
        )
    except (KeyError, ValueError):
        return content

    time_text = _format_time(current)
    date_text = (
        f"Hoje é {current.day} de {_MONTHS[current.month - 1]} "
        f"de {current.year}."
    )
    if intent is FastIntent.TIME:
        return time_text
    if intent is FastIntent.DATE:
        return date_text
    return f"{date_text[:-1]} e {time_text[0].casefold() + time_text[1:]}"


def _format_time(current: datetime) -> str:
    hour = current.hour
    minute = current.minute
    if hour == 1:
        base = "Agora é 1 hora"
    else:
        base = f"Agora são {hour} horas"
    if minute == 0:
        return base + "."
    if minute == 1:
        return base + " e 1 minuto."
    return base + f" e {minute} minutos."
