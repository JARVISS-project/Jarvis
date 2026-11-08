from enum import Enum
from functools import total_ordering


@total_ordering
class RiskLevel(str, Enum):
    READ_ONLY = "read_only"
    REVERSIBLE_LOCAL = "reversible_local"
    EXTERNAL_EFFECT = "external_effect"
    SENSITIVE = "sensitive"
    DESTRUCTIVE = "destructive"

    def _get_level(self) -> int:
        return {
            "read_only": 0,
            "reversible_local": 1,
            "external_effect": 2,
            "sensitive": 3,
            "destructive": 4,
        }[self.value]

    def __lt__(self, other: object) -> bool:
        if isinstance(other, RiskLevel):
            return self._get_level() < other._get_level()
        return NotImplemented

    def __le__(self, other: object) -> bool:
        if isinstance(other, RiskLevel):
            return self._get_level() <= other._get_level()
        return NotImplemented

    def __gt__(self, other: object) -> bool:
        if isinstance(other, RiskLevel):
            return self._get_level() > other._get_level()
        return NotImplemented

    def __ge__(self, other: object) -> bool:
        if isinstance(other, RiskLevel):
            return self._get_level() >= other._get_level()
        return NotImplemented