from enum import Enum


class RuntimeState(Enum):
    IDLE = "idle"
    ACTIVE = "active"
    CONFIRMATION = "confirmation"
    ACTIVE_CONVERSATION = "active_conversation"
    GAME_MODE = "game_mode"
