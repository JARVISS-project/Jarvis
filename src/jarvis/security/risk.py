from enum import IntEnum


class RiskLevel(IntEnum):
    SAFE = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4