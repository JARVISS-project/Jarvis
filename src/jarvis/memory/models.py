from dataclasses import dataclass
from datetime import datetime


@dataclass
class MemoryEntry:
    id: str
    content: str
    category: str
    created_at: datetime
