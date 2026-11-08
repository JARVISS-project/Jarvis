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
    CALENDAR_TODAY = "calendar_today"
    CALENDAR_TOMORROW = "calendar_tomorrow"
    CALENDAR_UPCOMING = "calendar_upcoming"
    CALENDAR_WEEKDAY = "calendar_weekday"
    CALENDAR_NEXT_WEEK = "calendar_next_week"
    CALENDAR_AVAILABILITY = "calendar_availability"


@dataclass(frozen=True)
class FastPathMatch:
    intent: FastIntent
    period: str | None = None
    start_time: str | None = None
    end_time: str | None = None


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
    _CALENDAR_TODAY_PATTERNS = (
        re.compile(
            _LEADING_FILLERS
            + _QUESTION_PREFIX
            + r"(?:(?:me )?(?:diz|diga|fala|fale|mostre|mostra|liste|lista)(?: ai)? )?"
            r"(?:o que (?:eu )?tenho hoje|o que tem hoje|tenho algo hoje|"
            r"compromissos de hoje|agenda de hoje|eventos de hoje)"
        ),
    )
    _CALENDAR_TOMORROW_PATTERNS = (
        re.compile(
            _LEADING_FILLERS
            + _QUESTION_PREFIX
            + r"(?:(?:me )?(?:diz|diga|fala|fale|mostre|mostra|liste|lista)(?: ai)? )?"
            r"(?:o que (?:eu )?tenho amanha|o que tem amanha|tenho algo amanha|"
            r"compromissos de amanha|agenda de amanha|eventos de amanha)"
        ),
    )
    _CALENDAR_UPCOMING_PATTERNS = (
        re.compile(
            _LEADING_FILLERS
            + _QUESTION_PREFIX
            + r"(?:(?:me )?(?:diz|diga|fala|fale|mostre|mostra|liste|lista)(?: ai)? )?"
            r"(?:proximos compromissos|proximos eventos|o que vem por ai|"
            r"o que tenho proximo|proximos dias|agenda dos proximos sete dias|"
            r"agenda dos proximos 7 dias|compromissos dos proximos sete dias)"
        ),
    )
    _CALENDAR_WEEKDAY_PATTERNS = (
        re.compile(
            _LEADING_FILLERS
            + _QUESTION_PREFIX
            + r"(?:(?:me )?(?:diz|diga|fala|fale|mostre|mostra|liste|lista)(?: ai)? )?"
            r"(?:o que (?:eu )?tenho (?:na |no )?(?:segunda|terca|terca-feira|quarta|quarta-feira|quinta|quinta-feira|sexta|sexta-feira|sabado|domingo)|"
            r"tenho algo (?:na |no )?(?:segunda|terca|terca-feira|quarta|quarta-feira|quinta|quinta-feira|sexta|sexta-feira|sabado|domingo)|"
            r"compromissos (?:na |no )?(?:segunda|terca|terca-feira|quarta|quarta-feira|quinta|quinta-feira|sexta|sexta-feira|sabado|domingo))"
        ),
    )
    _CALENDAR_NEXT_WEEK_PATTERNS = (
        re.compile(
            _LEADING_FILLERS
            + _QUESTION_PREFIX
            + r"(?:(?:me )?(?:diz|diga|fala|fale|mostre|mostra|liste|lista)(?: ai)? )?"
            r"(?:o que (?:eu )?tenho na proxima semana|agenda da proxima semana|"
            r"compromissos da proxima semana|eventos da proxima semana)"
        ),
    )
    _CALENDAR_AVAILABILITY_PATTERN = re.compile(
        _LEADING_FILLERS
        + _QUESTION_PREFIX
        + r"(?:(?:me )?(?:diz|diga|fala|fale|verifique|verifica|cheque|checa)(?: ai)? )?"
        r"(?:estou livre|tenho algo|estou ocupado|livre|ocupado) "
        r"(?P<day>hoje|amanha|(?:na |no )?(?:segunda|terca|terca-feira|"
        r"quarta|quarta-feira|quinta|quinta-feira|sexta|sexta-feira|sabado|domingo)) "
        r"(?:das|entre|de) (?P<start>\d{1,2}(?: \d{2})?) "
        r"(?:as|ate) (?P<end>\d{1,2}(?: \d{2})?)"
    )
    def match(self, message: str) -> FastPathMatch | None:
        normalized = self._repair_time_transcription(self.normalize(message))
        if any(pattern.fullmatch(normalized) for pattern in self._CALENDAR_TODAY_PATTERNS):
            return FastPathMatch(FastIntent.CALENDAR_TODAY, period="today")
        if any(pattern.fullmatch(normalized) for pattern in self._CALENDAR_TOMORROW_PATTERNS):
            return FastPathMatch(FastIntent.CALENDAR_TOMORROW, period="tomorrow")
        availability = self._CALENDAR_AVAILABILITY_PATTERN.fullmatch(normalized)
        if availability is not None:
            day = availability.group("day")
            if day == "hoje":
                period = "today"
            elif day == "amanha":
                period = "tomorrow"
            else:
                period = self._extract_weekday(day)
            return FastPathMatch(
                FastIntent.CALENDAR_AVAILABILITY,
                period=period,
                start_time=self._normalize_clock_time(availability.group("start")),
                end_time=self._normalize_clock_time(availability.group("end")),
            )
        if any(pattern.fullmatch(normalized) for pattern in self._CALENDAR_NEXT_WEEK_PATTERNS):
            return FastPathMatch(FastIntent.CALENDAR_NEXT_WEEK, period="next_week")
        if any(pattern.fullmatch(normalized) for pattern in self._CALENDAR_UPCOMING_PATTERNS):
            return FastPathMatch(FastIntent.CALENDAR_UPCOMING, period="upcoming")
        if any(pattern.fullmatch(normalized) for pattern in self._CALENDAR_WEEKDAY_PATTERNS):
            weekday = self._extract_weekday(normalized)
            return FastPathMatch(FastIntent.CALENDAR_WEEKDAY, period=weekday)
        if any(pattern.fullmatch(normalized) for pattern in self._DATE_TIME_PATTERNS):
            return FastPathMatch(FastIntent.DATE_TIME)
        if any(pattern.fullmatch(normalized) for pattern in self._TIME_PATTERNS):
            return FastPathMatch(FastIntent.TIME)
        if any(pattern.fullmatch(normalized) for pattern in self._DATE_PATTERNS):
            return FastPathMatch(FastIntent.DATE)
        return None

    def _extract_weekday(self, normalized: str) -> str:
        weekdays = {
            "segunda": "0",
            "terca": "1",
            "terca-feira": "1",
            "quarta": "2",
            "quarta-feira": "2",
            "quinta": "3",
            "quinta-feira": "3",
            "sexta": "4",
            "sexta-feira": "4",
            "sabado": "5",
            "domingo": "6",
        }
        for name, num in weekdays.items():
            if name in normalized:
                return f"weekday:{num}"
        return "weekday:0"

    @staticmethod
    def _normalize_clock_time(value: str) -> str:
        if " " not in value:
            return f"{int(value):02d}:00"
        hour, minute = value.split(" ", 1)
        return f"{int(hour):02d}:{int(minute):02d}"

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
