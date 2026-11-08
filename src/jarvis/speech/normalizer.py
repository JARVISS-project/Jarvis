"""Normalizações conservadoras para transcrições de comandos."""

import re


class TranscriptNormalizer:
    def normalize(self, text: str) -> str:
        path_context = bool(
            re.search(
                r"\bponto\s+[a-zA-Z0-9]+\b|\.[a-zA-Z0-9]{1,10}\b|\bbarra\b",
                text,
                flags=re.IGNORECASE,
            )
        )
        normalized = re.sub(
            r"(?<=\w)\s+ponto\s+([a-zA-Z0-9]+)\b",
            r".\1",
            text,
            flags=re.IGNORECASE,
        )
        if path_context:
            normalized = re.sub(
                r"(?<=\w)\s+(?:underline|underscore|sublinhado)\s+(?=\w)",
                "_",
                normalized,
                flags=re.IGNORECASE,
            )
            normalized = re.sub(
                r"(?<=\w)\s+h[ií]fen\s+(?=\w)",
                "-",
                normalized,
                flags=re.IGNORECASE,
            )
        path_pattern = re.compile(
            r"(?P<left>[\w.-]+)\s+barra\s+(?P<right>[\w.-]+)",
            flags=re.IGNORECASE,
        )
        while path_pattern.search(normalized):
            normalized = path_pattern.sub(r"\g<left>/\g<right>", normalized)
        return " ".join(normalized.split())
