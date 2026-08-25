from enum import Enum


class RuntimeState(Enum):
    IDLE = "idle"
    ACTIVE = "active"
    GAME_MODE = "game_mode"
