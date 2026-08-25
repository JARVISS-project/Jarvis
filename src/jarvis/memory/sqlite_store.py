import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from jarvis.memory.models import MemoryEntry
from jarvis.memory.store import MemoryStore


class SQLiteMemoryStore(MemoryStore):
    def __init__(self, database_path: Path | str) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._create_table()

    def add(self, content: str, category: str) -> MemoryEntry:
        entry = MemoryEntry(
            id=str(uuid4()),
            content=content,
            category=category,
            created_at=datetime.now(timezone.utc),
        )

        with closing(sqlite3.connect(self.database_path)) as connection:
            with connection:
                connection.execute(
                    """
                    INSERT INTO memories (id, content, category, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        entry.id,
                        entry.content,
                        entry.category,
                        entry.created_at.isoformat(),
                    ),
                )

        return entry

    def get_all(self) -> list[MemoryEntry]:
        with closing(sqlite3.connect(self.database_path)) as connection:
            rows = connection.execute(
                """
                SELECT id, content, category, created_at
                FROM memories
                ORDER BY creation_order
                """
            ).fetchall()

        return [
            MemoryEntry(
                id=row[0],
                content=row[1],
                category=row[2],
                created_at=datetime.fromisoformat(row[3]),
            )
            for row in rows
        ]

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
                        created_at TEXT NOT NULL
                    )
                    """
                )
