import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from jarvis.memory.models import (
    MemoryEntry,
    MemoryProvenance,
    MemorySensitivity,
    memory_relevance,
    memory_tokens,
)
from jarvis.memory.store import MemoryStore


class SQLiteMemoryStore(MemoryStore):
    def __init__(self, database_path: Path | str) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._create_table()

    def add(
        self,
        content: str,
        category: str,
        *,
        sensitivity: str = "private",
        provenance: str = "user_explicit",
        retention_days: int | None = None,
        consent_given: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryEntry:
        entry = MemoryEntry(
            id=uuid4().hex[:12],
            content=content,
            category=category,
            created_at=datetime.now(timezone.utc),
            sensitivity=MemorySensitivity(sensitivity),
            provenance=MemoryProvenance(provenance),
            retention_days=retention_days,
            consent_given=consent_given,
            metadata=metadata or {},
        )

        with closing(sqlite3.connect(self.database_path)) as connection:
            with connection:
                connection.execute(
                    """
                    INSERT INTO memories (
                        id, content, category, created_at,
                        sensitivity, provenance, retention_days,
                        consent_given, metadata
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entry.id,
                        entry.content,
                        entry.category,
                        entry.created_at.isoformat(),
                        entry.sensitivity.value,
                        entry.provenance.value,
                        entry.retention_days,
                        entry.consent_given,
                        json.dumps(entry.metadata, ensure_ascii=False),
                    ),
                )

        return entry

    def get_all(self) -> list[MemoryEntry]:
        self.cleanup_expired()
        with closing(sqlite3.connect(self.database_path)) as connection:
            rows = connection.execute(
                """
                SELECT
                    id, content, category, created_at,
                    sensitivity, provenance, retention_days,
                    consent_given, metadata
                FROM memories
                ORDER BY creation_order
                """
            ).fetchall()

        return [self._row_to_entry(row) for row in rows]

    def get_relevant(
        self,
        query: str,
        max_results: int = 5,
        min_score: float = 0.3,
    ) -> list[MemoryEntry]:
        self.cleanup_expired()
        if not query.strip():
            return []

        query_terms = memory_tokens(query)
        if not query_terms:
            return []

        with closing(sqlite3.connect(self.database_path)) as connection:
            rows = connection.execute(
                """
                SELECT
                    id, content, category, created_at,
                    sensitivity, provenance, retention_days,
                    consent_given, metadata
                FROM memories
                WHERE consent_given = 1
                """
            ).fetchall()

        scored: list[tuple[float, MemoryEntry]] = []
        for row in rows:
            entry = self._row_to_entry(row)
            if entry.is_expired():
                continue
            score = memory_relevance(entry, query_terms)
            if score >= min_score:
                scored.append((score, entry))

        scored.sort(key=lambda item: item[0], reverse=True)
        return [entry for _, entry in scored[:max_results]]

    def delete(self, memory_id: str) -> bool:
        with closing(sqlite3.connect(self.database_path)) as connection:
            with connection:
                cursor = connection.execute(
                    "DELETE FROM memories WHERE id = ?",
                    (memory_id,),
                )

        return cursor.rowcount > 0

    def clear(self) -> None:
        with closing(sqlite3.connect(self.database_path)) as connection:
            with connection:
                connection.execute("DELETE FROM memories")

    def cleanup_expired(self) -> int:
        with closing(sqlite3.connect(self.database_path)) as connection:
            with connection:
                cursor = connection.execute(
                    "DELETE FROM memories WHERE retention_days IS NOT NULL "
                    "AND julianday('now') - julianday(created_at) >= retention_days"
                )

        return cursor.rowcount

    def update_consent(self, memory_id: str, consent: bool) -> bool:
        with closing(sqlite3.connect(self.database_path)) as connection:
            with connection:
                cursor = connection.execute(
                    "UPDATE memories SET consent_given = ? WHERE id = ?",
                    (1 if consent else 0, memory_id),
                )

        return cursor.rowcount > 0

    def _create_table(self) -> None:
        with closing(sqlite3.connect(self.database_path)) as connection:
            with connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS memories (
                        creation_order INTEGER PRIMARY KEY AUTOINCREMENT,
                        id TEXT NOT NULL UNIQUE,
                        content TEXT NOT NULL,
                        category TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        sensitivity TEXT NOT NULL DEFAULT 'private',
                        provenance TEXT NOT NULL DEFAULT 'user_explicit',
                        retention_days INTEGER,
                        consent_given INTEGER NOT NULL DEFAULT 1,
                        metadata TEXT NOT NULL DEFAULT '{}'
                    )
                    """
                )
                columns = {
                    str(row[1])
                    for row in connection.execute(
                        "PRAGMA table_info(memories)"
                    ).fetchall()
                }
                migrations = {
                    "sensitivity": (
                        "ALTER TABLE memories ADD COLUMN sensitivity "
                        "TEXT NOT NULL DEFAULT 'private'"
                    ),
                    "provenance": (
                        "ALTER TABLE memories ADD COLUMN provenance "
                        "TEXT NOT NULL DEFAULT 'user_explicit'"
                    ),
                    "retention_days": (
                        "ALTER TABLE memories ADD COLUMN retention_days INTEGER"
                    ),
                    "consent_given": (
                        "ALTER TABLE memories ADD COLUMN consent_given "
                        "INTEGER NOT NULL DEFAULT 1"
                    ),
                    "metadata": (
                        "ALTER TABLE memories ADD COLUMN metadata "
                        "TEXT NOT NULL DEFAULT '{}'"
                    ),
                }
                for name, statement in migrations.items():
                    if name not in columns:
                        connection.execute(statement)

    @staticmethod
    def _row_to_entry(row: tuple) -> MemoryEntry:
        return MemoryEntry(
            id=row[0],
            content=row[1],
            category=row[2],
            created_at=datetime.fromisoformat(row[3]),
            sensitivity=MemorySensitivity(row[4]),
            provenance=MemoryProvenance(row[5]),
            retention_days=row[6] if row[6] is not None else None,
            consent_given=bool(row[7]),
            metadata=json.loads(row[8]) if row[8] else {},
        )
