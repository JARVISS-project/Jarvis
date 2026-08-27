"""Modelo visual puro da Ambient HUD.

O módulo não depende de Qt. Ele traduz estados do agente em parâmetros de
movimento/intensidade que qualquer renderer pode consumir no futuro.
"""

from __future__ import annotations

from dataclasses import dataclass

from jarvis.application.state import AgentState


@dataclass(frozen=True, slots=True)
class HUDProfile:
    """Parâmetros visuais de um estado do agente."""

    label: str
    code: str
    primary_speed: float
    secondary_speed: float
    pulse_speed: float
    activity: float
    standby_opacity: float
    scanner: bool = False
    waveform: bool = False
    telemetry: bool = True
    alert: bool = False


_PROFILES = {
    AgentState.WAITING_WAKE: HUDProfile(
        label="STANDBY",
        code="STB",
        primary_speed=0.010,
        secondary_speed=-0.006,
        pulse_speed=0.018,
        activity=0.20,
        standby_opacity=0.58,
        telemetry=False,
    ),
    AgentState.LISTENING: HUDProfile(
        label="LISTENING",
        code="LST",
        primary_speed=0.044,
        secondary_speed=-0.026,
        pulse_speed=0.075,
        activity=0.92,
        standby_opacity=1.0,
        scanner=True,
        waveform=True,
    ),
    AgentState.PROCESSING: HUDProfile(
        label="PROCESSING",
        code="PRC",
        primary_speed=0.082,
        secondary_speed=-0.058,
        pulse_speed=0.046,
        activity=1.0,
        standby_opacity=1.0,
        scanner=True,
    ),
    AgentState.SPEAKING: HUDProfile(
        label="SPEAKING",
        code="SPK",
        primary_speed=0.038,
        secondary_speed=-0.022,
        pulse_speed=0.095,
        activity=0.94,
        standby_opacity=1.0,
        waveform=True,
    ),
    AgentState.WAITING_FOLLOWUP: HUDProfile(
        label="FOLLOW-UP",
        code="FUP",
        primary_speed=0.025,
        secondary_speed=-0.014,
        pulse_speed=0.052,
        activity=0.56,
        standby_opacity=0.92,
        scanner=True,
        waveform=True,
    ),
    AgentState.WAITING_REPAIR: HUDProfile(
        label="REPAIR INPUT",
        code="RPR",
        primary_speed=0.030,
        secondary_speed=-0.017,
        pulse_speed=0.060,
        activity=0.62,
        standby_opacity=0.94,
        scanner=True,
        waveform=True,
    ),
    AgentState.CONFIRMATION: HUDProfile(
        label="CONFIRMATION",
        code="CNF",
        primary_speed=0.022,
        secondary_speed=-0.012,
        pulse_speed=0.058,
        activity=0.78,
        standby_opacity=1.0,
        scanner=True,
        alert=True,
    ),
    AgentState.EXECUTING: HUDProfile(
        label="EXECUTING",
        code="EXE",
        primary_speed=0.070,
        secondary_speed=-0.046,
        pulse_speed=0.052,
        activity=0.98,
        standby_opacity=1.0,
        scanner=True,
    ),
    AgentState.ERROR: HUDProfile(
        label="SYSTEM ALERT",
        code="ERR",
        primary_speed=0.025,
        secondary_speed=0.019,
        pulse_speed=0.090,
        activity=1.0,
        standby_opacity=1.0,
        scanner=False,
        alert=True,
    ),
}


def profile_for(state: AgentState) -> HUDProfile:
    """Retorna o perfil visual de ``state``."""

    return _PROFILES[state]


def label_for(state: AgentState) -> str:
    """Rótulo humano usado pela UI e pelo tray."""

    return profile_for(state).label
