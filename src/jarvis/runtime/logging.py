"""Logging técnico limitado e separado do Audit."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import re


_SECRET_PATTERN = re.compile(
    r"(?i)(authorization\s*[:=]\s*bearer\s+|"
    r"[\"']?(?:access|refresh|auth)?_?token[\"']?\s*[:=]\s*[\"']?|"
    r"[\"']?client[_ -]?secret[\"']?\s*[:=]\s*[\"']?|"
    r"[\"']?authorization[_ -]?code[\"']?\s*[:=]\s*[\"']?|"
    r"[\"']?pairing[_ -]?code[\"']?\s*[:=]\s*[\"']?)"
    r"([^\"'\s,;&}]+)"
)


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return _SECRET_PATTERN.sub(r"\1[REDACTED]", super().format(record))


def configure_runtime_logging(
    path: Path,
    *,
    max_bytes: int = 1_000_000,
    backup_count: int = 3,
) -> RotatingFileHandler:
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    handler.setFormatter(
        RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    logger = logging.getLogger("jarvis.runtime")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for current in tuple(logger.handlers):
        logger.removeHandler(current)
        current.close()
    logger.addHandler(handler)
    return handler
