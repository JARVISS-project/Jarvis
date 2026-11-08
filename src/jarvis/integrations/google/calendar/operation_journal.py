"""Creation IDs survive restart without storing event payloads or credentials."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
from threading import RLock
from uuid import uuid4

from jarvis.integrations.google.operation_lock import GoogleOperationLock


class CalendarOperationJournal:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self._values: dict[str, str] = {}
        self._lock = RLock()

    def _read(self) -> dict[str, str]:
        if self.path is None:
            return dict(self._values)
        if not self.path.exists():
            return {}
        try:
            if self.path.stat().st_size > 1024 * 1024:
                raise ValueError
            value = json.loads(self.path.read_text(encoding='utf-8'))
            if (not isinstance(value, dict) or len(value) > 4096
                    or any(not isinstance(k, str) or not re.fullmatch(r'[0-9a-f]{64}', k)
                           or not isinstance(v, str) or not re.fullmatch(r'[0-9a-f]{32}', v)
                           for k, v in value.items())):
                raise ValueError
            return value
        except (OSError, ValueError, TypeError):
            raise PermissionError('Journal Calendar inválido; reconcilie antes de escrever.') from None

    def _save(self, values: dict[str, str]) -> None:
        if self.path is None:
            self._values = dict(values)
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        try:
            with temporary.open('w', encoding='utf-8') as stream:
                stream.write(json.dumps(values, sort_keys=True))
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.path)
        except OSError:
            raise PermissionError('Não foi possível preservar o ID Calendar; escrita bloqueada.') from None

    def reserve(self, key: str) -> str:
        with self._lock, GoogleOperationLock(mutex_name=r'Local\JARVIS.CalendarWrite.Journal',
                                             lock_path=(self.path or Path('data/calendar-journal')).with_suffix('.lock')):
            values = self._read()
            if key in values:
                return values[key]
            if len(values) >= 4096:
                raise PermissionError('Journal Calendar cheio; reconcilie operações pendentes.')
            operation_id = uuid4().hex
            values[key] = operation_id
            self._save(values)
            return operation_id

    def complete(self, key: str, operation_id: str) -> None:
        with self._lock, GoogleOperationLock(mutex_name=r'Local\JARVIS.CalendarWrite.Journal',
                                             lock_path=(self.path or Path('data/calendar-journal')).with_suffix('.lock')):
            values = self._read()
            if values.get(key) != operation_id:
                raise PermissionError('Journal Calendar mudou; reconcilie a operação.')
            del values[key]
            self._save(values)
