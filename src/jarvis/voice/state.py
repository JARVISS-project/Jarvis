"""Estados observáveis do fluxo de voz, sem criar uma máquina complexa."""

from enum import Enum


class VoicePipelineState(Enum):
    WAITING_WAKE = "waiting_wake"
    LISTENING = "listening"
    PROCESSING = "processing"
    SPEAKING = "speaking"
    WAITING_FOLLOWUP = "waiting_followup"
    WAITING_REPAIR = "waiting_repair"
    CONFIRMATION = "confirmation"
    REQUEST_CONFIRMATION = "request_confirmation"
    WAITING_CONFIRMATION = "waiting_confirmation"
    CONFIRMED = "confirmed"
    DENIED = "denied"
    EXPIRED = "expired"
