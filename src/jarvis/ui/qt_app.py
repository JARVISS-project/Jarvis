"""Implementação Qt da Ambient HUD.

Este módulo é importado somente pelo launcher da UI. O Core não importa PySide6.
A linguagem visual usa geometria e animação próprias, sem assets de terceiros.
"""

from __future__ import annotations

import html
import math
import os
import sys
from pathlib import Path
from uuid import uuid4

from jarvis.application.text_interaction import TextEventBus
from jarvis.ui.administration import AdministrativeClient, MemoryPage, PermissionsPage
from jarvis.ui.conversation_preferences import ConversationPreferencesPage
from jarvis.ui.first_use import AvailabilityPanel, FirstUsePage
from jarvis.ui.application_catalog import ApplicationsPage
from jarvis.ui.routines import RoutinesPage
from jarvis.ui.organization import OrganizationPage
from jarvis.ui.runtime_control import RuntimeStopControl
from jarvis.ui.accounts import AccountsPage

from PySide6.QtCore import QObject, QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QAction,
    QColor,
    QCloseEvent,
    QFont,
    QIcon,
    QMouseEvent,
    QPainter,
    QPen,
    QPixmap,
    QPolygonF,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from jarvis.application.audio_activity import (
    AudioActivityBus,
    AudioActivitySnapshot,
)
from jarvis.application.conversation_log import (
    ConversationLogBus,
    ConversationLogSnapshot,
)
from jarvis.audio.devices import list_input_devices, probe_microphone
from jarvis.application.state import AgentState, AgentStateBus, StateSnapshot
from jarvis.application.telemetry import (
    TelemetryBus,
    TelemetrySnapshot,
)
from jarvis.ui.control_center_model import ControlCenterBus, ControlCenterSnapshot
from jarvis.ui.debug import AmbientUIDebugController
from jarvis.ui.hud_model import HUDProfile, label_for, profile_for
from jarvis.ui.panel_builder import build_ambient_panel
from jarvis.ui.layout import (
    EXPANDED_WINDOW_HEIGHT,
    HUD_DESIGN_SIZE,
    HUD_WIDGET_SIZE,
    PANEL_WIDTH,
    WINDOW_MARGIN,
    WINDOW_SPACING,
    collapsed_window_size,
    expanded_window_width,
)
from jarvis.ui.telemetry_model import format_telemetry


# Azul/ciano permanece a identidade principal. Tons de atenção aparecem somente
# nos estados que realmente precisam comunicar confirmação ou erro.
_STATE_PRESENTATION = {
    AgentState.WAITING_WAKE: ("Em espera", "Sempre presente, sem interromper."),
    AgentState.LISTENING: ("Ouvindo...", "Fale naturalmente."),
    AgentState.PROCESSING: ("Raciocinando...", "Conectando contexto e intenção."),
    AgentState.SPEAKING: ("Respondendo...", "Clareza primeiro."),
    AgentState.WAITING_FOLLOWUP: ("Ouvindo...", "Pode continuar."),
    AgentState.WAITING_REPAIR: ("Ouvindo...", "Pode repetir de outro jeito."),
    AgentState.CONFIRMATION: ("Confirmação", "Aguardando sua decisão."),
    AgentState.EXECUTING: ("Executando...", "Aplicando a ação autorizada."),
    AgentState.ERROR: ("Atenção", "Algo precisa da sua revisão."),
}


_STATE_COLORS = {
    AgentState.WAITING_WAKE: ((6, 22, 34), (67, 155, 187)),
    AgentState.LISTENING: ((6, 34, 50), (105, 207, 234)),
    AgentState.PROCESSING: ((7, 31, 49), (82, 174, 216)),
    AgentState.SPEAKING: ((6, 37, 53), (113, 210, 231)),
    AgentState.WAITING_FOLLOWUP: ((7, 31, 46), (88, 184, 214)),
    AgentState.WAITING_REPAIR: ((9, 30, 49), (100, 177, 219)),
    AgentState.CONFIRMATION: ((52, 39, 20), (218, 157, 79)),
    AgentState.EXECUTING: ((6, 33, 51), (83, 188, 225)),
    AgentState.ERROR: ((55, 23, 29), (207, 92, 104)),
}


class _StateRelay(QObject):
    snapshot_received = Signal(object)

    def relay(self, snapshot: StateSnapshot) -> None:
        self.snapshot_received.emit(snapshot)


class _TelemetryRelay(QObject):
    snapshot_received = Signal(object)

    def relay(self, snapshot: TelemetrySnapshot) -> None:
        self.snapshot_received.emit(snapshot)


class _AudioActivityRelay(QObject):
    snapshot_received = Signal(object)

    def relay(self, snapshot: AudioActivitySnapshot) -> None:
        self.snapshot_received.emit(snapshot)


class _ConversationLogRelay(QObject):
    snapshot_received = Signal(object)

    def relay(self, snapshot: ConversationLogSnapshot) -> None:
        self.snapshot_received.emit(snapshot)


class _ControlCenterRelay(QObject):
    snapshot_received = Signal(object)

    def relay(self, snapshot: ControlCenterSnapshot) -> None:
        self.snapshot_received.emit(snapshot)


class AmbientHUDWidget(QWidget):
    """HUD circular ambient desenhada por código, sem logotipo central."""

    clicked = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        widget_size: int = HUD_WIDGET_SIZE,
    ) -> None:
        super().__init__(parent)
        self.setFixedSize(widget_size, widget_size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._state = AgentState.WAITING_WAKE
        self._primary_phase = 0.0
        self._secondary_phase = 0.0
        self._pulse_phase = 0.0
        self._organic_time = 0.0
        self._audio_level_target = 0.0
        self._audio_level_display = 0.0
        self._press_position: QPoint | None = None
        self._last_drag_position: QPoint | None = None
        self._dragged = False

        self._animation = QTimer(self)
        self._animation.setInterval(30)
        self._animation.timeout.connect(self._advance_animation)
        self._animation.start()

    def set_state(self, state: AgentState) -> None:
        self._state = state
        if state not in {
            AgentState.LISTENING,
            AgentState.WAITING_FOLLOWUP,
            AgentState.WAITING_REPAIR,
            AgentState.CONFIRMATION,
        }:
            self._audio_level_target = 0.0
        self.update()

    def set_audio_activity(self, level: float) -> None:
        self._audio_level_target = max(0.0, min(1.0, float(level)))

    def _advance_animation(self) -> None:
        profile = profile_for(self._state)
        self._organic_time += 0.03
        target = self._audio_level_target
        smoothing = 0.46 if target > self._audio_level_display else 0.16
        self._audio_level_display += (
            target - self._audio_level_display
        ) * smoothing
        # Frames do recorder chegam em rajadas de ~80 ms; o decay mantém o
        # movimento fluido entre atualizações sem fabricar atividade contínua.
        self._audio_level_target *= 0.86
        # Variação quase-periódica: evita um ciclo curto perceptível que
        # pareça GIF. As velocidades respiram lentamente em frequências
        # diferentes e não sincronizadas.
        drift_a = 1.0 + 0.18 * math.sin(self._organic_time * 0.37)
        drift_b = 1.0 + 0.14 * math.sin(self._organic_time * 0.23 + 1.7)
        drift_p = 1.0 + 0.16 * math.sin(self._organic_time * 0.19 + 0.8)
        self._primary_phase += profile.primary_speed * drift_a
        self._secondary_phase += profile.secondary_speed * drift_b
        self._pulse_phase += profile.pulse_speed * drift_p
        self.update()

    @staticmethod
    def _point(center: QPointF, radius: float, degrees: float) -> QPointF:
        radians = math.radians(degrees)
        return QPointF(
            center.x() + math.cos(radians) * radius,
            center.y() + math.sin(radians) * radius,
        )

    @staticmethod
    def _arc_rect(center: QPointF, radius: float) -> QRectF:
        return QRectF(
            center.x() - radius,
            center.y() - radius,
            radius * 2.0,
            radius * 2.0,
        )

    @staticmethod
    def _arc(
        painter: QPainter,
        center: QPointF,
        radius: float,
        start: float,
        span: float,
        color: QColor,
        width: float = 1.0,
    ) -> None:
        pen = QPen(color, width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawArc(
            AmbientHUDWidget._arc_rect(center, radius),
            int(start * 16),
            int(span * 16),
        )

    def _draw_ambient_field(
        self,
        painter: QPainter,
        center: QPointF,
        inner_rgb: tuple[int, int, int],
        accent_rgb: tuple[int, int, int],
        pulse: float,
        profile: HUDProfile,
    ) -> None:
        radius = 121.0 + pulse * 3.0
        gradient = QRadialGradient(center, radius)
        gradient.setColorAt(0.0, QColor(*accent_rgb, int(22 * profile.activity)))
        gradient.setColorAt(0.38, QColor(*inner_rgb, int(18 * profile.activity)))
        gradient.setColorAt(0.76, QColor(*inner_rgb, int(8 * profile.activity)))
        gradient.setColorAt(1.0, QColor(*inner_rgb, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(gradient)
        painter.drawEllipse(self._arc_rect(center, radius))

    def _draw_reference_geometry(
        self,
        painter: QPainter,
        center: QPointF,
        accent_rgb: tuple[int, int, int],
        profile: HUDProfile,
    ) -> None:
        alpha = int(44 + 32 * profile.activity)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(*accent_rgb, alpha), 0.8))

        for radius in (43.0, 75.0, 105.0):
            painter.drawEllipse(self._arc_rect(center, radius))

        # Crosshair fragmentado: cria orientação espacial sem fechar uma mira.
        for angle in (0.0, 90.0, 180.0, 270.0):
            start = self._point(center, 112.0, angle)
            end = self._point(center, 122.0, angle)
            painter.drawLine(start, end)

    def _draw_ticks(
        self,
        painter: QPainter,
        center: QPointF,
        accent_rgb: tuple[int, int, int],
        profile: HUDProfile,
    ) -> None:
        rotation = math.degrees(self._secondary_phase) * 0.35
        for index in range(72):
            major = index % 6 == 0
            medium = index % 3 == 0
            angle = index * 5.0 + rotation
            inner_radius = 112.0 if major else 116.0 if medium else 118.5
            outer_radius = 124.0 if major else 123.0
            alpha = 36 if not major else 105
            alpha = int(alpha * (0.42 + 0.58 * profile.activity))
            if self._state is AgentState.WAITING_WAKE and not major:
                alpha = 12
            pen = QPen(
                QColor(*accent_rgb, alpha),
                1.25 if major else 0.75,
            )
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawLine(
                self._point(center, inner_radius, angle),
                self._point(center, outer_radius, angle),
            )

    def _draw_arc_layers(
        self,
        painter: QPainter,
        center: QPointF,
        accent_rgb: tuple[int, int, int],
        pulse: float,
        profile: HUDProfile,
    ) -> None:
        primary = math.degrees(self._primary_phase)
        secondary = math.degrees(self._secondary_phase)

        layers = (
            (104.0, primary * 0.65, ((46.0, 8.0), (23.0, 116.0), (62.0, 211.0))),
            (91.0, secondary * 0.82, ((35.0, 31.0), (52.0, 166.0), (18.0, 284.0))),
            (74.0, -primary * 1.08, ((29.0, 12.0), (73.0, 128.0), (31.0, 259.0))),
            (56.0, secondary * 1.28, ((42.0, 42.0), (24.0, 196.0))),
        )

        max_layers = 2 if self._state is AgentState.WAITING_WAKE else len(layers)
        for layer_index, (radius, rotation, arcs) in enumerate(layers[:max_layers]):
            for arc_index, (span, offset) in enumerate(arcs):
                alpha = int(
                    (72 + 78 * profile.activity)
                    * (1.0 - layer_index * 0.10)
                )
                width = 0.9 if layer_index % 2 else 1.25
                if arc_index == 0 and profile.activity > 0.7:
                    alpha = min(220, alpha + 34)
                    width += 0.45
                self._arc(
                    painter,
                    center,
                    radius,
                    rotation + offset,
                    span + pulse * (4.0 if layer_index == 2 else 1.5),
                    QColor(*accent_rgb, alpha),
                    width,
                )

        # Pequenos conectores tangenciais dão sensação de montagem dinâmica.
        if self._state is not AgentState.WAITING_WAKE:
            for index, angle in enumerate((18.0, 138.0, 252.0)):
                dynamic = angle + primary * (0.22 if index != 1 else -0.18)
                p1 = self._point(center, 75.0, dynamic)
                p2 = self._point(center, 88.0, dynamic)
                painter.setPen(QPen(QColor(*accent_rgb, 92), 0.8))
                painter.drawLine(p1, p2)

    def _draw_scanner(
        self,
        painter: QPainter,
        center: QPointF,
        accent_rgb: tuple[int, int, int],
        pulse: float,
        profile: HUDProfile,
    ) -> None:
        if not profile.scanner:
            return

        angle = math.degrees(self._primary_phase) * 1.65 - 90.0
        start = self._point(center, 34.0, angle)
        end = self._point(center, 101.0, angle)
        painter.setPen(QPen(QColor(*accent_rgb, 76), 0.85))
        painter.drawLine(start, end)

        point = self._point(center, 101.0, angle)
        dot = QRadialGradient(point, 7.0 + pulse * 2.0)
        dot.setColorAt(0.0, QColor(235, 253, 255, 230))
        dot.setColorAt(0.28, QColor(*accent_rgb, 210))
        dot.setColorAt(1.0, QColor(*accent_rgb, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(dot)
        painter.drawEllipse(self._arc_rect(point, 7.0 + pulse * 2.0))

    def _draw_waveform(
        self,
        painter: QPainter,
        center: QPointF,
        accent_rgb: tuple[int, int, int],
        profile: HUDProfile,
    ) -> None:
        if not profile.waveform:
            return

        speaking = self._state is AgentState.SPEAKING
        followup = self._state in {
            AgentState.WAITING_FOLLOWUP,
            AgentState.WAITING_REPAIR,
        }
        base_radius = 47.0
        for index in range(48):
            angle = index * 7.5
            wave = (
                math.sin(self._pulse_phase * 3.1 + index * 0.72)
                + 0.55 * math.sin(self._pulse_phase * 1.7 - index * 0.39)
                + 0.25 * math.sin(index * 1.61)
            ) / 1.8
            amplitude = 3.0 + ((wave + 1.0) / 2.0) * (12.0 if speaking else 8.0)
            if followup:
                amplitude *= 0.55
            start = self._point(center, base_radius, angle)
            end = self._point(center, base_radius + amplitude, angle)
            alpha = 178 if speaking else 132
            if followup:
                alpha = 78
            pen = QPen(QColor(*accent_rgb, alpha), 1.0)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawLine(start, end)

    def _draw_center_presence(
        self,
        painter: QPainter,
        center: QPointF,
        accent_rgb: tuple[int, int, int],
        pulse: float,
        profile: HUDProfile,
    ) -> None:
        # Sem esfera ou logotipo: apenas uma presença luminosa mínima e retículo.
        painter.setBrush(Qt.BrushStyle.NoBrush)
        self._arc(
            painter,
            center,
            31.0,
            math.degrees(self._secondary_phase) * 1.7 + 12.0,
            72.0,
            QColor(*accent_rgb, int(96 + 62 * profile.activity)),
            1.0,
        )
        self._arc(
            painter,
            center,
            31.0,
            math.degrees(self._secondary_phase) * 1.7 + 176.0,
            41.0,
            QColor(207, 247, 255, 122),
            0.85,
        )

        for angle in (0.0, 90.0, 180.0, 270.0):
            painter.setPen(QPen(QColor(*accent_rgb, 84), 0.75))
            painter.drawLine(
                self._point(center, 21.0, angle),
                self._point(center, 26.0, angle),
            )

        dot_radius = 2.1 + pulse * (1.1 if profile.activity > 0.6 else 0.45)
        glow = QRadialGradient(center, 12.0)
        glow.setColorAt(0.0, QColor(248, 255, 255, 255))
        glow.setColorAt(0.18, QColor(*accent_rgb, 220))
        glow.setColorAt(0.55, QColor(*accent_rgb, 58))
        glow.setColorAt(1.0, QColor(*accent_rgb, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(self._arc_rect(center, 12.0))
        painter.setBrush(QColor(233, 253, 255, 245))
        painter.drawEllipse(self._arc_rect(center, dot_radius))

    def _draw_data_nodes(
        self,
        painter: QPainter,
        center: QPointF,
        accent_rgb: tuple[int, int, int],
        profile: HUDProfile,
    ) -> None:
        if not profile.telemetry:
            return

        rotation = math.degrees(self._secondary_phase) * 0.42
        nodes = (
            (67.0, rotation + 18.0),
            (89.0, rotation + 143.0),
            (105.0, rotation + 266.0),
        )
        for index, (radius, angle) in enumerate(nodes):
            point = self._point(center, radius, angle)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(*accent_rgb, 150 - index * 22))
            painter.drawEllipse(self._arc_rect(point, 1.7 if index == 0 else 1.2))

    def _draw_state_overlay(
        self,
        painter: QPainter,
        center: QPointF,
        accent_rgb: tuple[int, int, int],
        pulse: float,
    ) -> None:
        if self._state is AgentState.EXECUTING:
            rotation = -math.degrees(self._primary_phase) * 1.05
            for index in range(3):
                self._arc(
                    painter,
                    center,
                    130.0,
                    rotation + index * 120.0,
                    28.0 + pulse * 4.0,
                    QColor(*accent_rgb, 138),
                    1.15,
                )
        elif self._state is AgentState.CONFIRMATION:
            radius = 128.0 + pulse * 1.5
            points = QPolygonF(
                [
                    self._point(center, radius, -90.0),
                    self._point(center, radius, 30.0),
                    self._point(center, radius, 150.0),
                ]
            )
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(*accent_rgb, 96), 0.85))
            painter.drawPolygon(points)
        elif self._state is AgentState.ERROR:
            # Glitch geométrico sutil: arcos deslocados, não uma tela vermelha.
            offset = 2.5 * math.sin(self._pulse_phase * 4.0)
            self._arc(
                painter,
                QPointF(center.x() + offset, center.y()),
                104.0,
                17.0,
                64.0,
                QColor(*accent_rgb, 196),
                1.4,
            )
            self._arc(
                painter,
                QPointF(center.x() - offset, center.y()),
                91.0,
                198.0,
                48.0,
                QColor(*accent_rgb, 174),
                1.2,
            )

    def _draw_micro_telemetry(
        self,
        painter: QPainter,
        center: QPointF,
        accent_rgb: tuple[int, int, int],
        profile: HUDProfile,
    ) -> None:
        if not profile.telemetry:
            return

        font = QFont("Segoe UI", 6)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.0)
        painter.setFont(font)
        painter.setPen(QColor(*accent_rgb, 104))

        painter.drawText(
            QRectF(center.x() - 103.0, center.y() - 114.0, 70.0, 14.0),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            f"{profile.code} // LOCAL",
        )
        painter.drawText(
            QRectF(center.x() + 36.0, center.y() + 99.0, 72.0, 14.0),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            "CORE // LOCAL",
        )

        # Linhas de telemetria ficam deliberadamente abstratas no modo demo;
        # dados reais serão conectados posteriormente pelo application layer.
        painter.setPen(QPen(QColor(*accent_rgb, 62), 0.7))
        painter.drawLine(
            QPointF(center.x() - 105.0, center.y() - 96.0),
            QPointF(center.x() - 76.0, center.y() - 96.0),
        )
        painter.drawLine(
            QPointF(center.x() + 78.0, center.y() + 95.0),
            QPointF(center.x() + 106.0, center.y() + 95.0),
        )

    def _draw_approved_identity_orb(
        self,
        painter: QPainter,
        center: QPointF,
        inner_rgb: tuple[int, int, int],
        accent_rgb: tuple[int, int, int],
        pulse: float,
        profile: HUDProfile,
    ) -> None:
        listening = self._state in {
            AgentState.LISTENING,
            AgentState.WAITING_FOLLOWUP,
            AgentState.WAITING_REPAIR,
            AgentState.CONFIRMATION,
        }
        processing = self._state in {
            AgentState.PROCESSING,
            AgentState.EXECUTING,
        }
        speaking = self._state is AgentState.SPEAKING

        sphere_radius = 67.0 + pulse * (1.8 if profile.activity > 0.5 else 0.8)

        # Campo externo deliberadamente contido: presença, não um neon espalhado.
        field = QRadialGradient(center, 112.0)
        field.setColorAt(0.0, QColor(*accent_rgb, 24))
        field.setColorAt(0.50, QColor(*inner_rgb, 18))
        field.setColorAt(1.0, QColor(*inner_rgb, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(field)
        painter.drawEllipse(self._arc_rect(center, 112.0))

        # Guias quase imperceptíveis dão leitura de sistema sem poluir.
        painter.setBrush(Qt.BrushStyle.NoBrush)
        guide_pen = QPen(QColor(*accent_rgb, 28), 0.75)
        guide_pen.setStyle(Qt.PenStyle.DotLine)
        painter.setPen(guide_pen)
        painter.drawEllipse(self._arc_rect(center, 94.0))
        painter.drawEllipse(self._arc_rect(center, 108.0))

        # Esfera principal: vidro escuro com núcleo ciano suave.
        focal_drift = QPointF(
            center.x() - 12.0 + 5.0 * math.sin(self._organic_time * 0.29),
            center.y() - 16.0 + 4.0 * math.cos(self._organic_time * 0.21),
        )
        sphere = QRadialGradient(
            focal_drift,
            sphere_radius * 1.28,
            center,
        )
        sphere.setColorAt(0.0, QColor(167, 231, 244, 96))
        sphere.setColorAt(0.22, QColor(*accent_rgb, 72))
        sphere.setColorAt(0.58, QColor(*inner_rgb, 224))
        sphere.setColorAt(0.90, QColor(3, 13, 22, 246))
        sphere.setColorAt(1.0, QColor(*accent_rgb, 118))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(sphere)
        painter.drawEllipse(self._arc_rect(center, sphere_radius))

        # Movimento interno contínuo e discreto: duas trilhas luminosas com
        # frequências diferentes evitam a sensação de objeto estático.
        inner_rotation = math.degrees(self._organic_time * 0.17)
        self._arc(
            painter,
            center,
            52.0,
            inner_rotation + 24.0,
            72.0,
            QColor(*accent_rgb, 44),
            1.0,
        )
        self._arc(
            painter,
            center,
            59.0,
            -inner_rotation * 0.73 + 196.0,
            48.0,
            QColor(213, 241, 247, 30),
            0.8,
        )
        inner_glint = self._point(
            center,
            55.0,
            inner_rotation * 1.31 + 118.0,
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(*accent_rgb, 74))
        painter.drawEllipse(self._arc_rect(inner_glint, 1.45))

        rim = QPen(QColor(*accent_rgb, int(118 + 62 * profile.activity)), 1.35)
        rim.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(rim)
        painter.drawEllipse(self._arc_rect(center, sphere_radius))

        inner_rim = QPen(QColor(198, 238, 247, 54), 0.75)
        painter.setPen(inner_rim)
        painter.drawArc(
            self._arc_rect(center, sphere_radius - 4.0),
            206 * 16,
            108 * 16,
        )

        # Acento âmbar é a assinatura visual; pequeno de propósito.
        amber_angle = (
            38.0
            + 5.5 * math.sin(self._organic_time * 0.17)
            + 2.5 * math.sin(self._organic_time * 0.41 + 0.9)
        )
        if processing:
            amber_angle += math.degrees(self._primary_phase) * 0.72
        elif speaking:
            amber_angle += math.sin(self._pulse_phase) * 9.0
        amber_point = self._point(center, 91.0, amber_angle)
        amber = QRadialGradient(amber_point, 7.0)
        amber.setColorAt(0.0, QColor(255, 233, 196, 245))
        amber.setColorAt(0.30, QColor(219, 155, 76, 210))
        amber.setColorAt(1.0, QColor(219, 155, 76, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(amber)
        painter.drawEllipse(self._arc_rect(amber_point, 7.0))

        if self._state is AgentState.WAITING_WAKE:
            idle_rotation = math.degrees(self._primary_phase) * 0.42
            self._arc(
                painter,
                center,
                84.0,
                idle_rotation + 18.0,
                54.0,
                QColor(*accent_rgb, 58),
                0.9,
            )
            idle_node = self._point(
                center,
                96.0,
                -math.degrees(self._secondary_phase) * 0.31 + 214.0,
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(*accent_rgb, 96))
            painter.drawEllipse(self._arc_rect(idle_node, 1.35))

        if listening:
            self._draw_internal_listening_wave(painter, center, accent_rgb)
        elif processing:
            self._draw_processing_identity(painter, center, accent_rgb)
        elif speaking:
            self._draw_speaking_identity(painter, center, accent_rgb, pulse)
        elif self._state is AgentState.ERROR:
            self._arc(
                painter,
                center,
                81.0,
                205.0,
                96.0,
                QColor(*accent_rgb, 150),
                1.6,
            )

        # Núcleo permanece discreto; ajuda a esfera a parecer viva sem logotipo.
        core_radius = 10.0 + 1.4 * pulse
        core = QRadialGradient(center, core_radius * 2.2)
        core.setColorAt(0.0, QColor(228, 251, 255, 235))
        core.setColorAt(0.24, QColor(*accent_rgb, 178))
        core.setColorAt(1.0, QColor(*accent_rgb, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(core)
        painter.drawEllipse(self._arc_rect(center, core_radius * 2.2))

    def _draw_internal_listening_wave(
        self,
        painter: QPainter,
        center: QPointF,
        accent_rgb: tuple[int, int, int],
    ) -> None:
        # A onda fica integralmente dentro da esfera e reage ao nível real do
        # microfone. Voz mais alta = barras mais altas, sem expandir o widget.
        level = max(0.025, min(1.0, self._audio_level_display))
        painter.setPen(QPen(QColor(*accent_rgb, 46), 0.7))
        painter.drawLine(
            QPointF(center.x() - 48.0, center.y()),
            QPointF(center.x() + 48.0, center.y()),
        )

        bars = 21
        spacing = 4.15
        middle = (bars - 1) / 2.0
        for index in range(bars):
            distance = abs(index - middle) / middle
            envelope = 0.30 + 0.70 * (1.0 - distance**1.65)
            texture = 0.68 + 0.32 * abs(
                math.sin(index * 0.91 + self._pulse_phase * 2.2)
            )
            half_height = 2.0 + (4.0 + 23.0 * level) * envelope * texture
            x = center.x() + (index - middle) * spacing
            alpha = int(78 + 148 * min(1.0, level + envelope * 0.12))
            pen = QPen(QColor(*accent_rgb, alpha), 1.25)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawLine(
                QPointF(x, center.y() - half_height),
                QPointF(x, center.y() + half_height),
            )

    def _draw_processing_identity(
        self,
        painter: QPainter,
        center: QPointF,
        accent_rgb: tuple[int, int, int],
    ) -> None:
        rotation = math.degrees(self._primary_phase)
        for radius, start, span, alpha in (
            (80.0, rotation + 18.0, 64.0, 138),
            (88.0, -rotation * 0.62 + 148.0, 42.0, 94),
            (98.0, rotation * 0.38 + 252.0, 31.0, 66),
        ):
            self._arc(
                painter,
                center,
                radius,
                start,
                span,
                QColor(*accent_rgb, alpha),
                1.15,
            )
        for offset, radius in ((0.0, 80.0), (126.0, 88.0), (242.0, 98.0)):
            point = self._point(center, radius, rotation + offset)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(*accent_rgb, 176))
            painter.drawEllipse(self._arc_rect(point, 1.9))

    def _draw_speaking_identity(
        self,
        painter: QPainter,
        center: QPointF,
        accent_rgb: tuple[int, int, int],
        pulse: float,
    ) -> None:
        # A fala deve reagir ao envelope REAL do TTS. O pulso orgânico entra
        # apenas como microtextura; intensidade e pausas vêm do áudio.
        level = max(0.0, min(1.0, self._audio_level_display))

        bars = 19
        spacing = 4.35
        middle = (bars - 1) / 2.0
        painter.setPen(QPen(QColor(*accent_rgb, 38), 0.7))
        painter.drawLine(
            QPointF(center.x() - 44.0, center.y()),
            QPointF(center.x() + 44.0, center.y()),
        )
        for index in range(bars):
            distance = abs(index - middle) / middle
            envelope = 0.34 + 0.66 * (1.0 - distance**1.7)
            texture = 0.72 + 0.28 * abs(
                math.sin(
                    index * 0.83
                    + self._organic_time * 2.7
                    + pulse * 0.9
                )
            )
            half_height = 1.4 + (2.8 + 21.0 * level) * envelope * texture
            x = center.x() + (index - middle) * spacing
            alpha = int(68 + 168 * min(1.0, level + envelope * 0.10))
            pen = QPen(QColor(*accent_rgb, alpha), 1.35)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawLine(
                QPointF(x, center.y() - half_height),
                QPointF(x, center.y() + half_height),
            )

        # A borda respira junto com a energia da voz; em pausas quase some.
        for index in range(2):
            radius = 76.0 + index * 9.0 + level * (4.0 + index * 2.5)
            alpha = int((26 + level * 94) * (1.0 - index * 0.28))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(*accent_rgb, alpha), 0.95))
            painter.drawEllipse(self._arc_rect(center, radius))

    def paintEvent(self, _event: object) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        scale = min(self.width(), self.height()) / float(HUD_DESIGN_SIZE)
        painter.scale(scale, scale)

        inner_rgb, accent_rgb = _STATE_COLORS[self._state]
        profile = profile_for(self._state)
        center = QPointF(HUD_DESIGN_SIZE / 2.0, HUD_DESIGN_SIZE / 2.0)
        pulse_wave = (
            math.sin(self._pulse_phase)
            + 0.34 * math.sin(self._organic_time * 0.71 + 1.3)
            + 0.18 * math.sin(self._organic_time * 1.13 + 2.1)
        )
        pulse = max(0.0, min(1.0, 0.5 + pulse_wave / 3.04))

        self._draw_approved_identity_orb(
            painter,
            center,
            inner_rgb,
            accent_rgb,
            pulse,
            profile,
        )
        painter.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() is Qt.MouseButton.LeftButton:
            global_position = event.globalPosition().toPoint()
            self._press_position = global_position
            self._last_drag_position = global_position
            self._dragged = False
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if (
            self._press_position is not None
            and self._last_drag_position is not None
            and event.buttons() & Qt.MouseButton.LeftButton
        ):
            global_position = event.globalPosition().toPoint()
            if (global_position - self._press_position).manhattanLength() > 5:
                self._dragged = True
            delta = global_position - self._last_drag_position
            if self._dragged:
                self.window().move(self.window().pos() + delta)
            self._last_drag_position = global_position
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() is Qt.MouseButton.LeftButton:
            if not self._dragged:
                self.clicked.emit()
            self._press_position = None
            self._last_drag_position = None
            self._dragged = False
            event.accept()
            return
        super().mouseReleaseEvent(event)


class HUDPanelFrame(QFrame):
    """Painel compacto com cantos técnicos desenhados sobre o frame."""

    def paintEvent(self, event: object) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(91, 214, 247, 105), 1.0))
        size = 15.0
        margin = 5.0
        width = float(self.width())
        height = float(self.height())

        segments = (
            (QPointF(margin, margin + size), QPointF(margin, margin), QPointF(margin + size, margin)),
            (QPointF(width - margin - size, margin), QPointF(width - margin, margin), QPointF(width - margin, margin + size)),
            (QPointF(margin, height - margin - size), QPointF(margin, height - margin), QPointF(margin + size, height - margin)),
            (QPointF(width - margin - size, height - margin), QPointF(width - margin, height - margin), QPointF(width - margin, height - margin - size)),
        )
        for first, corner, second in segments:
            painter.drawLine(first, corner)
            painter.drawLine(corner, second)
        painter.end()


class ControlCenterWindow(QWidget):
    """Control Center visual separado do orb ambiente."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        user_display_name: str = "Usuário",
        agent_name: str = "Jarvis",
        identity_saver=None,
        current_microphone: str | None = None,
        microphone_saver=None,
        on_command=None,
    ) -> None:
        super().__init__(parent)
        self.user_display_name = user_display_name.strip() or "Usuário"
        self.agent_name = agent_name.strip() or "Jarvis"
        self.identity_saver = identity_saver
        self.current_microphone = current_microphone
        self.microphone_saver = microphone_saver
        self.on_command = on_command
        self._text_generation = None
        self.administrative_client = AdministrativeClient(on_command, self)
        self._text_interaction = None
        self._text_challenge = None
        self._text_busy = False
        self._text_log = ConversationLogBus()
        self._conversation_snapshot = ConversationLogSnapshot()
        self.setWindowTitle("JARVIS Control Center")
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.resize(1180, 720)
        self.setMinimumSize(980, 620)
        self.setObjectName("controlCenter")
        self.setStyleSheet(
            """
            QWidget#controlCenter {
                background: #07111b;
                color: #d9e8ee;
            }
            QFrame#sidebar {
                background: rgba(5, 14, 23, 245);
                border-right: 1px solid rgba(91, 178, 205, 34);
            }
            QLabel#brand {
                color: rgba(232, 244, 248, 242);
                font-family: "Segoe UI";
                font-size: 24px;
                font-weight: 300;
            }
            QLabel#brandTagline {
                color: rgba(128, 165, 178, 185);
                font-family: "Segoe UI";
                font-size: 9px;
            }
            QPushButton#nav {
                color: rgba(151, 184, 195, 210);
                background: transparent;
                border: 0;
                border-radius: 8px;
                text-align: left;
                padding: 11px 14px;
                font-family: "Segoe UI";
                font-size: 12px;
            }
            QPushButton#nav:hover {
                background: rgba(67, 154, 184, 20);
                color: rgba(210, 235, 242, 235);
            }
            QPushButton#navActive {
                color: rgb(166, 224, 240);
                background: rgba(60, 148, 181, 28);
                border-left: 2px solid rgb(103, 207, 234);
                border-radius: 7px;
                text-align: left;
                padding: 11px 12px;
                font-family: "Segoe UI";
                font-size: 12px;
            }
            QLabel#sectionEyebrow {
                color: rgba(218, 157, 79, 210);
                font-family: "Segoe UI";
                font-size: 8px;
            }
            QLabel#heroTitle {
                color: rgba(231, 244, 248, 244);
                font-family: "Segoe UI";
                font-size: 30px;
                font-weight: 300;
            }
            QLabel#heroSub {
                color: rgba(123, 162, 176, 198);
                font-family: "Segoe UI";
                font-size: 11px;
            }
            QLabel#stateTitle {
                color: rgba(231, 244, 248, 245);
                font-family: "Segoe UI";
                font-size: 25px;
                font-weight: 300;
            }
            QLabel#stateSub {
                color: rgba(119, 163, 179, 205);
                font-family: "Segoe UI";
                font-size: 10px;
            }
            QFrame#infoCard {
                background: rgba(7, 20, 31, 218);
                border: 1px solid rgba(91, 178, 205, 52);
                border-radius: 12px;
            }
            QLabel#cardTitle {
                color: rgba(178, 217, 229, 230);
                font-family: "Segoe UI";
                font-size: 10px;
                font-weight: 600;
            }
            QLabel#cardPrimary {
                color: rgba(228, 241, 245, 238);
                font-family: "Segoe UI";
                font-size: 15px;
                font-weight: 400;
            }
            QLabel#cardSecondary {
                color: rgba(116, 158, 173, 198);
                font-family: "Segoe UI";
                font-size: 10px;
            }
            QLabel#conversationMessage {
                color: rgba(190, 214, 222, 230);
                font-family: "Segoe UI";
                font-size: 13px;
            }
            QLabel#conversationRecent {
                color: rgba(175, 196, 204, 230);
                font-family: "Segoe UI";
                font-size: 11px;
            }
            QLabel#footer {
                color: rgba(99, 137, 150, 150);
                font-family: "Segoe UI";
                font-size: 8px;
            }
            QFrame#conversationPanel {
                background: rgba(7, 20, 31, 218);
                border: 1px solid rgba(91, 178, 205, 48);
                border-radius: 12px;
            }
            QFrame#conversationSession {
                background: rgba(36, 93, 118, 82);
                border: 1px solid rgba(81, 198, 231, 135);
                border-radius: 10px;
            }
            QFrame#userBubble {
                background: rgba(35, 93, 124, 175);
                border: 1px solid rgba(83, 181, 215, 72);
                border-radius: 11px;
            }
            QFrame#assistantBubble {
                background: rgba(14, 31, 44, 235);
                border: 1px solid rgba(91, 178, 205, 42);
                border-radius: 11px;
            }
            QPushButton#conversationAction {
                color: rgba(166, 205, 217, 215);
                background: rgba(11, 29, 43, 210);
                border: 1px solid rgba(91, 178, 205, 54);
                border-radius: 8px;
                padding: 9px 12px;
                text-align: left;
            }
            QPushButton#conversationAction:disabled {
                color: rgba(102, 136, 148, 135);
                border-color: rgba(91, 178, 205, 28);
                background: rgba(9, 22, 33, 145);
            }
            QScrollArea#conversationScroll {
                background: transparent;
                border: 0;
            }
            QScrollArea#conversationScroll > QWidget > QWidget {
                background: transparent;
            }
            QDialog {
                background: #08131e;
                color: #d9e8ee;
            }
            QLineEdit {
                background: rgba(7, 20, 31, 235);
                color: rgba(230, 243, 247, 242);
                border: 1px solid rgba(91, 178, 205, 65);
                border-radius: 8px;
                padding: 9px 11px;
                selection-background-color: rgba(76, 168, 198, 115);
            }
            QDialogButtonBox QPushButton {
                min-width: 84px;
                padding: 8px 12px;
                border-radius: 7px;
                background: rgba(60, 148, 181, 30);
                color: rgba(210, 235, 242, 235);
                border: 1px solid rgba(91, 178, 205, 48);
            }
            """
        )

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        sidebar = QFrame(self)
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(205)
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(26, 32, 22, 24)
        side_layout.setSpacing(8)

        brand = QLabel("J A R V I S")
        brand.setObjectName("brand")
        self.user_label = QLabel(self.user_display_name)
        self.user_label.setObjectName("brandTagline")
        self.agent_identity_label = QLabel(f"Agente · {self.agent_name}")
        self.agent_identity_label.setObjectName("brandTagline")
        side_layout.addWidget(brand)
        side_layout.addSpacing(8)
        side_layout.addWidget(self.user_label)
        side_layout.addWidget(self.agent_identity_label)
        side_layout.addSpacing(30)

        self.nav_buttons: dict[str, QPushButton] = {}
        for index, label in enumerate(
            ("Início", "Conversa", "Memória", "Permissões", "Aplicativos", "Organização de arquivos", "Contas e conexões", "Rotinas", "Ferramentas", "Ajustes", "Ajuda")
        ):
            button = QPushButton(label)
            button.setObjectName("navActive" if index == 0 else "nav")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            self.nav_buttons[label] = button
            if label == "Início":
                button.clicked.connect(lambda: self._show_page("Início"))
            elif label == "Conversa":
                button.clicked.connect(lambda: self._show_page("Conversa"))
            elif label in {"Memória", "Permissões", "Aplicativos", "Organização de arquivos", "Contas e conexões", "Rotinas", "Ajuda"}:
                button.clicked.connect(lambda _checked=False, name=label: self._show_page(name))
            elif label == "Ajustes":
                button.clicked.connect(lambda: self._show_page("Ajustes"))
            elif label == "Ferramentas":
                button.clicked.connect(lambda: self._show_page("Ajuda"))
            side_layout.addWidget(button)
        side_layout.addStretch(1)
        self.google_documents_button = QPushButton('Documentos Google')
        self.google_documents_button.setToolTip('Selecionar documentos Google')
        self.google_documents_button.clicked.connect(self.open_google_documents)
        side_layout.addWidget(self.google_documents_button)
        root.addWidget(sidebar)

        content = QWidget(self)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(46, 30, 44, 26)
        content_layout.setSpacing(18)

        header = QHBoxLayout()
        header_copy = QVBoxLayout()
        self.agent_title_label = QLabel(self.agent_name)
        self.agent_title_label.setObjectName("heroTitle")
        subtitle = QLabel("Control Center")
        subtitle.setObjectName("heroSub")
        header_copy.addWidget(self.agent_title_label)
        header_copy.addWidget(subtitle)
        header.addLayout(header_copy)
        header.addStretch(1)
        self.runtime_label = QLabel("LOCAL · AGUARDANDO CONEXÃO")
        self.runtime_label.setObjectName("heroSub")
        header.addWidget(self.runtime_label, 0, Qt.AlignmentFlag.AlignTop)
        self.runtime_stop = RuntimeStopControl(self.administrative_client, self)
        header.addWidget(self.runtime_stop, 0, Qt.AlignmentFlag.AlignTop)
        content_layout.addLayout(header)

        body = QHBoxLayout()
        body.setSpacing(28)

        center_column = QVBoxLayout()
        center_column.setSpacing(2)
        center_column.addStretch(1)
        self.main_orb = AmbientHUDWidget(self, widget_size=360)
        self.main_orb.setCursor(Qt.CursorShape.ArrowCursor)
        center_column.addWidget(
            self.main_orb,
            0,
            Qt.AlignmentFlag.AlignHCenter,
        )
        self.main_state_label = QLabel("Em espera")
        self.main_state_label.setObjectName("stateTitle")
        self.main_state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.main_state_subtitle = QLabel("Sempre presente, sem interromper.")
        self.main_state_subtitle.setObjectName("stateSub")
        self.main_state_subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        center_column.addWidget(self.main_state_label)
        center_column.addWidget(self.main_state_subtitle)
        center_column.addStretch(1)
        body.addLayout(center_column, 1)

        cards = QVBoxLayout()
        cards.setSpacing(16)
        agenda, self.agenda_primary, self.agenda_secondary = self._make_card(
            "AGENDA",
            "Nenhum evento carregado",
            "A agenda real aparece aqui quando a integração estiver disponível.",
        )
        focus, self.focus_primary, self.focus_secondary = self._make_card(
            "FOCO DE HOJE",
            "Pronto para a próxima tarefa",
            "O estado do agente muda sem lotar sua tela.",
        )
        system, self.system_primary, self.system_secondary = self._make_card(
            "SISTEMA",
            "Aguardando conexão",
            "Abra o runtime local para conversar.",
        )
        conversation = self._make_conversation_card()
        agenda.hide()
        focus.hide()
        self.availability_panel = AvailabilityPanel(self.administrative_client, self._show_page, self)
        self.availability_panel.setMinimumWidth(330)
        cards.addWidget(self.availability_panel, 1)
        system.hide()
        conversation.hide()
        body.addLayout(cards, 0)
        content_layout.addLayout(body, 1)

        self.home_page = content
        self.conversation_page = self._make_conversation_page()
        self.page_stack = QStackedWidget(self)
        self.page_stack.addWidget(self.home_page)
        self.page_stack.addWidget(self.conversation_page)
        self.memory_page = MemoryPage(self.administrative_client, self)
        self.permissions_page = PermissionsPage(self.administrative_client, self)
        self.page_stack.addWidget(self.memory_page)
        self.page_stack.addWidget(self.permissions_page)
        self.applications_page = ApplicationsPage(self.administrative_client, self._submit_catalog_command, self)
        self.page_stack.addWidget(self.applications_page)
        self.organization_page = OrganizationPage(self.administrative_client, self._submit_catalog_command, self,
            cancel_command=self._cancel_text)
        self.page_stack.addWidget(self.organization_page)
        self.accounts_page = AccountsPage(self.administrative_client, self._submit_catalog_command, self)
        self.page_stack.addWidget(self.accounts_page)
        self.preferences_page = ConversationPreferencesPage(self.administrative_client, self,
                                                          identity_changed=self._apply_user_name)
        identity_button = QPushButton("Identidade e microfone")
        identity_button.clicked.connect(self.open_identity_settings)
        self.preferences_page.layout.addWidget(identity_button)
        self.page_stack.addWidget(self.preferences_page)
        self.help_page = FirstUsePage(self.administrative_client, self._show_page, self)
        self.page_stack.addWidget(self.help_page)
        self.routines_page = RoutinesPage(self.administrative_client, self._submit_catalog_command, self._cancel_text, self)
        self.page_stack.addWidget(self.routines_page)
        root.addWidget(self.page_stack, 1)
        self._show_page("Início")

    def _show_page(self, page_name: str) -> None:
        if not hasattr(self, "page_stack"):
            return
        page_index = {"Início": 0, "Conversa": 1, "Memória": 2, "Permissões": 3, "Aplicativos": 4, "Organização de arquivos": 5, "Contas e conexões": 6, "Ajustes": 7, "Ajuda": 8, "Rotinas": 9}.get(page_name, 0)
        self.page_stack.setCurrentIndex(page_index)
        active_name = page_name if page_index else "Início"
        if page_index in {2, 3, 4, 5, 6, 7, 8, 9}:
            self.page_stack.currentWidget().refresh()
        elif page_index == 0:
            self.availability_panel.refresh()
        for label, button in self.nav_buttons.items():
            button.setObjectName("navActive" if label == active_name else "nav")
            button.style().unpolish(button)
            button.style().polish(button)
            button.update()

    def _on_ptt_pressed(self) -> None:
        if self.on_command:
            self.on_command({"command": "ptt_start"})

    def _apply_user_name(self, name: str) -> None:
        self.user_display_name = name
        self.user_label.setTextFormat(Qt.TextFormat.PlainText)
        self.user_label.setText(name)
        self.set_conversation_log(self._conversation_snapshot)

    def _on_ptt_released(self) -> None:
        if self.on_command:
            self.on_command({"command": "ptt_stop"})

    def _text_controls(self) -> None:
        ready = self._text_generation is not None and callable(self.on_command)
        self.conversation_input.setReadOnly(not ready)
        waiting = self._text_challenge is not None
        self.conversation_input.setEnabled(ready and not self._text_busy and not waiting)
        self.conversation_send.setEnabled(ready and not self._text_busy and not waiting)
        self.conversation_confirm.setEnabled(ready and waiting and not self._text_busy)
        self.conversation_cancel.setEnabled(ready and (waiting or self._text_busy))

    def _submit_catalog_command(self, text):
        if self._text_busy or self._text_challenge or not self._text_generation:
            self._show_page("Conversa")
            self.conversation_status.setText(
                "Revise e confirme ou cancele a ação pendente antes de enviar outra."
                if self._text_challenge else "Aguarde o pedido atual ou cancele antes de enviar outro."
                if self._text_busy else "Runtime desconectado. Reabra o JARVIS para conversar.")
            return None
        self.conversation_input.setText(text)
        self._show_page("Conversa")
        self._send_text()
        return self._text_generation, self._text_interaction

    def _send_text(self) -> None:
        text = self.conversation_input.text().strip()
        if not text or self._text_busy or self._text_challenge or not self._text_generation:
            return
        self._text_interaction = uuid4().hex
        self._text_busy = True
        self._text_log.add_user(text)
        self.set_conversation_log(self._text_log.snapshot)
        self.conversation_input.clear()
        self.conversation_status.setText("Processando…")
        self._text_controls()
        self.on_command({"command": "text_submit", "text": text,
                         "generation": self._text_generation, "interaction": self._text_interaction})

    def _confirm_text(self) -> None:
        if self._text_busy or not self._text_challenge or not self._text_generation:
            return
        session, challenge = self._text_challenge
        self._text_busy = True
        self._text_controls()
        self.on_command({"command": "text_confirm", "generation": self._text_generation,
                         "interaction": self._text_interaction, "session": session, "challenge": challenge})

    def _cancel_text(self) -> None:
        if self._text_generation and self._text_interaction:
            self.on_command({"command": "text_cancel", "generation": self._text_generation,
                             "interaction": self._text_interaction})
            self._text_challenge = None
            self.conversation_confirm.setEnabled(False)

    def apply_text_event(self, event: dict) -> None:
        self.administrative_client.apply_event(event)
        if event.get("type") == "admin":
            return
        self.applications_page.handle_text_event(event)
        self.organization_page.handle_text_event(event)
        status = event.get("status")
        if status == "ready":
            previous_generation = self._text_generation
            self._text_generation = event.get("generation")
            self._text_interaction = None
            self._text_challenge = None
            self._text_busy = False
            self.conversation_status.setText("Pronto")
            self.runtime_label.setText("LOCAL · CONECTADO")
            self.runtime_stop.connected()
            self.system_primary.setText("Conectado")
            self.availability_panel.refresh()
            QTimer.singleShot(0, self.help_page.check_startup)
            if self.page_stack.currentWidget() is self.accounts_page and (
                    previous_generation != self._text_generation or not self.accounts_page.data):
                self.accounts_page.refresh()
        elif status == "disconnected":
            self._text_generation = None
            self._text_challenge = None
            self._text_busy = False
            self.conversation_status.setText("Runtime desconectado; nenhuma mensagem será reenviada.")
            self.runtime_label.setText("LOCAL · DESCONECTADO")
            self.runtime_stop.disconnected()
            self.system_primary.setText("Desconectado")
            self.availability_panel.disconnected()
        elif (event.get("generation") == self._text_generation
              and event.get("interaction") == self._text_interaction):
            self._text_busy = status in {"busy", "speaking"}
            self._text_challenge = ((event["session"], event["challenge"])
                                    if status == "confirmation" else None)
            text = event.get("text", "")
            label = "Falando…" if status == "speaking" else (text or "Processando…") if self._text_busy else (
                "Revise ação, alvo e efeito na prévia acima. Confirmar autoriza somente essa operação; "
                "Cancelar impede a execução. O resultado aparecerá aqui após verificação."
                if status == "confirmation"
                else text if status == "error" else "Cancelado" if status == "cancelled" else "Pronto"
            )
            self.conversation_status.setText(label)
            if text and status != "busy":
                self._text_log.add_assistant(text)
                self.set_conversation_log(self._text_log.snapshot)
        self._text_controls()

    def hideEvent(self, event) -> None:
        self._on_ptt_released()
        super().hideEvent(event)

    def _make_conversation_page(self) -> QWidget:
        page = QWidget(self)
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(34, 28, 34, 26)
        page_layout.setSpacing(18)

        header = QHBoxLayout()
        copy = QVBoxLayout()
        title = QLabel("Conversa")
        title.setObjectName("heroTitle")
        subtitle = QLabel(
            "Histórico desta sessão, mantido somente em memória."
        )
        subtitle.setObjectName("heroSub")
        copy.addWidget(title)
        copy.addWidget(subtitle)
        header.addLayout(copy)
        header.addStretch(1)
        ephemeral = QLabel("SESSÃO EFÊMERA  ·  NÃO PERSISTE EM DISCO")
        ephemeral.setObjectName("sectionEyebrow")
        header.addWidget(ephemeral, 0, Qt.AlignmentFlag.AlignTop)
        page_layout.addLayout(header)

        body = QHBoxLayout()
        body.setSpacing(14)

        sessions_panel = QFrame(page)
        sessions_panel.setObjectName("conversationPanel")
        sessions_panel.setFixedWidth(225)
        sessions_layout = QVBoxLayout(sessions_panel)
        sessions_layout.setContentsMargins(16, 16, 16, 16)
        sessions_layout.setSpacing(10)
        sessions_title = QLabel("CONVERSAS DESTA SESSÃO")
        sessions_title.setObjectName("cardTitle")
        sessions_layout.addWidget(sessions_title)

        session_card = QFrame(sessions_panel)
        session_card.setObjectName("conversationSession")
        session_layout = QVBoxLayout(session_card)
        session_layout.setContentsMargins(13, 12, 13, 12)
        session_layout.setSpacing(5)
        self.conversation_session_title = QLabel("Sessão atual")
        self.conversation_session_title.setObjectName("cardPrimary")
        self.conversation_session_preview = QLabel("Nenhuma mensagem ainda.")
        self.conversation_session_preview.setTextFormat(Qt.TextFormat.PlainText)
        self.conversation_session_preview.setObjectName("cardSecondary")
        self.conversation_session_preview.setWordWrap(True)
        self.conversation_session_meta = QLabel("Somente memória")
        self.conversation_session_meta.setObjectName("footer")
        session_layout.addWidget(self.conversation_session_title)
        session_layout.addWidget(self.conversation_session_preview)
        session_layout.addWidget(self.conversation_session_meta)
        sessions_layout.addWidget(session_card)
        sessions_layout.addStretch(1)
        privacy_hint = QLabel(
            "O JARVIS não cria conversas antigas fictícias e não grava "
            "este histórico automaticamente."
        )
        privacy_hint.setObjectName("footer")
        privacy_hint.setWordWrap(True)
        sessions_layout.addWidget(privacy_hint)

        self.ptt_button = QPushButton("Segure para falar")
        self.ptt_button.setObjectName("actionPrimary")
        self.ptt_button.setFixedHeight(40)
        self.ptt_button.setEnabled(callable(self.on_command))
        self.ptt_button.pressed.connect(self._on_ptt_pressed)
        self.ptt_button.released.connect(self._on_ptt_released)
        sessions_layout.addWidget(self.ptt_button)

        body.addWidget(sessions_panel)

        thread_panel = QFrame(page)
        thread_panel.setObjectName("conversationPanel")
        thread_layout = QVBoxLayout(thread_panel)
        thread_layout.setContentsMargins(16, 14, 16, 14)
        thread_layout.setSpacing(10)

        thread_header = QHBoxLayout()
        thread_copy = QVBoxLayout()
        self.conversation_thread_title = QLabel("Sessão atual")
        self.conversation_thread_title.setObjectName("cardPrimary")
        thread_subtitle = QLabel(
            "Mensagens de texto, falas aceitas e respostas finais do agente."
        )
        thread_subtitle.setObjectName("cardSecondary")
        thread_copy.addWidget(self.conversation_thread_title)
        thread_copy.addWidget(thread_subtitle)
        thread_header.addLayout(thread_copy)
        thread_header.addStretch(1)
        self.conversation_message_count = QLabel("0 mensagens")
        self.conversation_message_count.setObjectName("footer")
        thread_header.addWidget(
            self.conversation_message_count,
            0,
            Qt.AlignmentFlag.AlignTop,
        )
        thread_layout.addLayout(thread_header)

        self.conversation_scroll = QScrollArea(thread_panel)
        self.conversation_scroll.setObjectName("conversationScroll")
        self.conversation_scroll.setWidgetResizable(True)
        self.conversation_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.conversation_messages_container = QWidget()
        self.conversation_messages_layout = QVBoxLayout(
            self.conversation_messages_container
        )
        self.conversation_messages_layout.setContentsMargins(2, 8, 2, 8)
        self.conversation_messages_layout.setSpacing(10)
        self.conversation_scroll.setWidget(self.conversation_messages_container)
        thread_layout.addWidget(self.conversation_scroll, 1)

        input_row = QHBoxLayout()
        input_row.setSpacing(8)
        self.conversation_input = QLineEdit()
        self.conversation_input.setReadOnly(True)
        self.conversation_input.setMaxLength(2000)
        self.conversation_input.setPlaceholderText("Digite uma mensagem")
        self.conversation_input.setEnabled(False)
        self.conversation_send = QPushButton("Enviar")
        self.conversation_send.setObjectName("conversationAction")
        self.conversation_send.setEnabled(False)
        self.conversation_send.clicked.connect(self._send_text)
        self.conversation_input.returnPressed.connect(self._send_text)
        self.conversation_confirm = QPushButton("Confirmar ação")
        self.conversation_confirm.setEnabled(False)
        self.conversation_confirm.clicked.connect(self._confirm_text)
        self.conversation_confirm.setObjectName("conversationAction")
        self.conversation_cancel = QPushButton("Cancelar")
        self.conversation_cancel.setEnabled(False)
        self.conversation_cancel.clicked.connect(self._cancel_text)
        self.conversation_cancel.setObjectName("conversationAction")
        input_row.addWidget(self.conversation_input, 1)
        input_row.addWidget(self.conversation_send)
        thread_layout.addLayout(input_row)
        confirmation_row = QHBoxLayout()
        confirmation_row.addWidget(self.conversation_confirm)
        confirmation_row.addWidget(self.conversation_cancel)
        confirmation_row.addStretch(1)
        thread_layout.addLayout(confirmation_row)
        self.conversation_status = QLabel("Runtime textual desconectado")
        self.conversation_status.setObjectName("cardSecondary")
        self.conversation_status.setTextFormat(Qt.TextFormat.PlainText)
        self.conversation_status.setWordWrap(True)
        thread_layout.addWidget(self.conversation_status)
        body.addWidget(thread_panel, 1)

        details_panel = QFrame(page)
        details_panel.setObjectName("conversationPanel")
        details_panel.setFixedWidth(225)
        details_layout = QVBoxLayout(details_panel)
        details_layout.setContentsMargins(16, 16, 16, 16)
        details_layout.setSpacing(12)
        details_title = QLabel("DETALHES DA CONVERSA")
        details_title.setObjectName("cardTitle")
        details_layout.addWidget(details_title)
        self.conversation_details = QLabel()
        self.conversation_details.setObjectName("cardSecondary")
        self.conversation_details.setTextFormat(Qt.TextFormat.RichText)
        self.conversation_details.setWordWrap(True)
        details_layout.addWidget(self.conversation_details)

        privacy_title = QLabel("PRIVACIDADE")
        privacy_title.setObjectName("cardTitle")
        details_layout.addSpacing(8)
        details_layout.addWidget(privacy_title)
        privacy = QLabel(
            "Transcrições ficam somente neste buffer local em memória. "
            "Retries, debug e raciocínio interno não aparecem aqui."
        )
        privacy.setObjectName("cardSecondary")
        privacy.setWordWrap(True)
        details_layout.addWidget(privacy)

        actions_title = QLabel("AÇÕES")
        actions_title.setObjectName("cardTitle")
        details_layout.addSpacing(8)
        details_layout.addWidget(actions_title)
        self.conversation_export_button = QPushButton("Exportar conversa")
        self.conversation_export_button.setObjectName("conversationAction")
        self.conversation_export_button.setEnabled(False)
        self.conversation_export_button.setToolTip(
            "Exportação não foi habilitada para preservar a política atual "
            "de não persistir transcrições."
        )
        self.conversation_clear_button = QPushButton("Limpar conversa")
        self.conversation_clear_button.setObjectName("conversationAction")
        self.conversation_clear_button.setEnabled(False)
        self.conversation_clear_button.setToolTip(
            "A UI permanece observadora; ela não altera o histórico do Core."
        )
        details_layout.addWidget(self.conversation_export_button)
        details_layout.addWidget(self.conversation_clear_button)
        details_layout.addStretch(1)
        body.addWidget(details_panel)

        page_layout.addLayout(body, 1)
        self._refresh_conversation_page()
        return page

    def _conversation_message_widget(self, entry) -> QWidget:
        row = QWidget(self.conversation_messages_container)
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(8)

        bubble = QFrame(row)
        bubble.setObjectName(
            "userBubble" if entry.role == "user" else "assistantBubble"
        )
        bubble.setMaximumWidth(520)
        bubble.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Minimum,
        )
        bubble_layout = QVBoxLayout(bubble)
        bubble_layout.setContentsMargins(13, 10, 13, 10)
        bubble_layout.setSpacing(5)

        role_name = (
            self.user_display_name if entry.role == "user" else self.agent_name
        )
        role = QLabel(role_name)
        role.setObjectName("cardTitle")
        if entry.role == "user":
            role.setStyleSheet("color: rgba(218, 157, 79, 225);")
        else:
            role.setStyleSheet("color: rgba(105, 201, 231, 235);")

        message = QLabel(entry.text)
        message.setTextFormat(Qt.TextFormat.PlainText)
        message.setObjectName("conversationMessage")
        message.setWordWrap(True)
        message.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        sequence = QLabel(f"Mensagem {entry.sequence:02d}")
        sequence.setObjectName("footer")
        sequence.setAlignment(Qt.AlignmentFlag.AlignRight)

        bubble_layout.addWidget(role)
        bubble_layout.addWidget(message)
        bubble_layout.addWidget(sequence)

        if entry.role == "user":
            row_layout.addStretch(1)
            row_layout.addWidget(bubble)
        else:
            row_layout.addWidget(bubble)
            row_layout.addStretch(1)
        return row

    def _refresh_conversation_page(self) -> None:
        if not hasattr(self, "conversation_messages_layout"):
            return

        while self.conversation_messages_layout.count():
            item = self.conversation_messages_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        entries = self._conversation_snapshot.entries
        count = len(entries)
        self.conversation_message_count.setText(
            f"{count} mensagem" if count == 1 else f"{count} mensagens"
        )

        first_user = next(
            (entry.text for entry in entries if entry.role == "user"),
            "",
        )
        if first_user:
            preview = first_user if len(first_user) <= 72 else first_user[:69] + "..."
            self.conversation_session_preview.setText(preview)
        else:
            self.conversation_session_preview.setText("Nenhuma mensagem ainda.")

        user_count = sum(1 for entry in entries if entry.role == "user")
        status = "Ativa" if entries else "Aguardando"
        self.conversation_details.setText(
            "<div style='line-height:1.55'>"
            f"<b style='color:#b2d9e5'>Status</b><br>{status}<br><br>"
            f"<b style='color:#b2d9e5'>Mensagens</b><br>{count}<br><br>"
            f"<b style='color:#b2d9e5'>Turnos do usuário</b><br>{user_count}<br><br>"
            "<b style='color:#b2d9e5'>Armazenamento</b><br>Somente memória<br><br>"
            "<b style='color:#b2d9e5'>Persistência</b><br>Desativada"
            "</div>"
        )

        if not entries:
            empty = QLabel(
                "Envie uma mensagem por texto ou use a voz para iniciar a conversa."
            )
            empty.setObjectName("cardSecondary")
            empty.setWordWrap(True)
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setMinimumHeight(180)
            self.conversation_messages_layout.addWidget(empty)
        else:
            for entry in entries:
                self.conversation_messages_layout.addWidget(
                    self._conversation_message_widget(entry)
                )
        self.conversation_messages_layout.addStretch(1)

    def _make_card(
        self,
        title: str,
        primary: str,
        secondary: str,
    ) -> tuple[QFrame, QLabel, QLabel]:
        card = QFrame(self)
        card.setObjectName("infoCard")
        card.setFixedWidth(330)
        card.setMinimumHeight(104)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(8)
        title_label = QLabel(title)
        title_label.setObjectName("cardTitle")
        primary_label = QLabel(primary)
        primary_label.setObjectName("cardPrimary")
        primary_label.setWordWrap(True)
        secondary_label = QLabel(secondary)
        secondary_label.setObjectName("cardSecondary")
        secondary_label.setWordWrap(True)
        layout.addWidget(title_label)
        layout.addStretch(1)
        layout.addWidget(primary_label)
        layout.addWidget(secondary_label)
        return card, primary_label, secondary_label

    def _make_conversation_card(self) -> QFrame:
        card = QFrame(self)
        card.setObjectName("infoCard")
        card.setFixedWidth(330)
        card.setMinimumHeight(172)
        card.setMaximumHeight(205)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(8)

        title = QLabel("CONVERSA RECENTE")
        title.setObjectName("cardTitle")
        self.conversation_body = QLabel(
            "<span style='color:rgba(116,158,173,0.82)'>"
            "Nenhuma conversa nesta sessão.</span>"
        )
        self.conversation_body.setObjectName("conversationRecent")
        self.conversation_body.setTextFormat(Qt.TextFormat.RichText)
        self.conversation_body.setWordWrap(True)
        self.conversation_body.setAlignment(
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft
        )

        layout.addWidget(title)
        layout.addWidget(self.conversation_body, 1)
        return card

    def set_conversation_log(
        self,
        snapshot: ConversationLogSnapshot,
    ) -> None:
        self._conversation_snapshot = snapshot
        self._refresh_conversation_page()
        recent = snapshot.entries[-4:]
        if not recent:
            self.conversation_body.setText(
                "<span style='color:#748f9b'>"
                "Nenhuma conversa nesta sessão.</span>"
            )
            return

        rows: list[str] = []
        for entry in recent:
            label = (
                self.user_display_name
                if entry.role == "user"
                else self.agent_name
            )
            label_color = (
                "#d99d4f" if entry.role == "user" else "#69c9e7"
            )
            safe_label = html.escape(label)
            safe_text = html.escape(entry.text)
            if len(safe_text) > 180:
                safe_text = safe_text[:177] + "..."
            rows.append(
                f"<div style='margin-bottom:7px'>"
                f"<span style='color:{label_color};font-weight:600'>"
                f"{safe_label}</span><br>"
                f"<span style='color:#afc4cc'>{safe_text}</span>"
                f"</div>"
            )
        self.conversation_body.setText("".join(rows))

    def open_google_documents(self) -> None:
        from jarvis.ui.google_documents import GoogleDocumentsDialog
        dialog = GoogleDocumentsDialog(self)
        dialog.exec()

    def open_identity_settings(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Identidade")
        dialog.setModal(True)
        dialog.setMinimumWidth(380)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(22, 22, 22, 18)
        layout.setSpacing(10)

        user_label = QLabel("Nome do usuário")
        user_label.setObjectName("cardTitle")
        user_input = QLineEdit(self.user_display_name)
        user_input.setPlaceholderText("Como você quer aparecer na interface")

        agent_label = QLabel("Nome do agente")
        agent_label.setObjectName("cardTitle")
        agent_input = QLineEdit(self.agent_name)
        agent_input.setPlaceholderText("Nome escolhido para o agente")

        mic_label = QLabel("Microfone")
        mic_label.setObjectName("cardTitle")

        mic_layout = QHBoxLayout()
        mic_combo = QComboBox()
        mic_combo.addItem("Padrão do Sistema", userData=None)

        devices = list_input_devices()
        current_mic_index = 0
        for i, d in enumerate(devices):
            mic_combo.addItem(d.name, userData=d.name)
            if self.current_microphone and d.name == self.current_microphone:
                current_mic_index = i + 1
        mic_combo.setCurrentIndex(current_mic_index)

        test_mic_btn = QPushButton("Testar")
        test_mic_btn.setCursor(Qt.CursorShape.PointingHandCursor)

        mic_layout.addWidget(mic_combo)
        mic_layout.addWidget(test_mic_btn)

        status_label = QLabel("")
        status_label.setObjectName("cardSecondary")
        status_label.setWordWrap(True)
        if self.current_microphone and current_mic_index == 0:
            status_label.setText(
                "Dispositivo configurado indisponível; usando padrão do sistema."
            )

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )

        layout.addWidget(user_label)
        layout.addWidget(user_input)
        layout.addSpacing(4)
        layout.addWidget(agent_label)
        layout.addWidget(agent_input)
        layout.addSpacing(4)
        layout.addWidget(mic_label)
        layout.addLayout(mic_layout)
        layout.addWidget(status_label)
        layout.addSpacing(8)
        layout.addWidget(buttons)

        def test_microphone():
            test_mic_btn.setEnabled(False)
            status_label.setText("Testando microfone...")
            QApplication.processEvents()

            selected_name = mic_combo.currentData()

            target_index = -1
            if selected_name is not None:
                for d in devices:
                    if d.name == selected_name:
                        target_index = d.index
                        break

            success = probe_microphone(target_index)
            if success:
                status_label.setText("Microfone funcionando!")
            else:
                status_label.setText("Falha ao acessar o microfone.")
            test_mic_btn.setEnabled(True)

        test_mic_btn.clicked.connect(test_microphone)

        def save_identity() -> None:
            user_name = user_input.text().strip()
            agent_name = agent_input.text().strip()
            if not user_name or not agent_name:
                status_label.setText("Os dois nomes precisam estar preenchidos.")
                return
            try:
                if self.identity_saver is not None:
                    self.identity_saver(
                        user_display_name=user_name,
                        agent_name=agent_name,
                    )
                if self.microphone_saver is not None:
                    selected_mic = mic_combo.currentData()
                    self.microphone_saver(selected_mic)
                    self.current_microphone = selected_mic
            except (OSError, ValueError) as error:
                status_label.setText(str(error))
                return

            self.user_display_name = user_name
            self.agent_name = agent_name
            self.user_label.setText(user_name)
            self.agent_identity_label.setText(f"Agente · {agent_name}")
            self.agent_title_label.setText(agent_name)
            self.set_conversation_log(self._conversation_snapshot)
            dialog.accept()

        buttons.accepted.connect(save_identity)
        buttons.rejected.connect(dialog.reject)
        dialog.exec()

    def set_state(self, state: AgentState) -> None:
        self.main_orb.set_state(state)
        title, subtitle = _STATE_PRESENTATION[state]
        self.main_state_label.setText(title)
        self.main_state_subtitle.setText(subtitle)
        self.focus_primary.setText(title)
        self.focus_secondary.setText(subtitle)

    def set_audio_activity(self, level: float) -> None:
        self.main_orb.set_audio_activity(level)

    def set_telemetry(self, snapshot: TelemetrySnapshot) -> None:
        if snapshot.has_completed_turn:
            self.system_secondary.setText(
                f"Último turno {snapshot.turn_ms} ms  ·  "
                f"LLM {snapshot.llm_ms} ms  ·  Tools {snapshot.tool_calls}"
            )
        else:
            self.system_secondary.setText("Runtime local conectado.")

    def set_control_center(self, snapshot: ControlCenterSnapshot) -> None:
        self.system_primary.setText("Online")
        self.system_secondary.setText(
            f"Memória {snapshot.memory.total}  ·  "
            f"Skills {snapshot.skills.total}  ·  "
            f"Visão {'ativa' if snapshot.vision.enabled else 'inativa'}"
        )


class AmbientWindow(QWidget):
    """Janela flutuante da Ambient HUD com painel e system tray."""

    def __init__(
        self,
        state_bus: AgentStateBus,
        *,
        debug_mode: bool,
        telemetry_bus: TelemetryBus | None = None,
        control_center_bus: ControlCenterBus | None = None,
        audio_activity_bus: AudioActivityBus | None = None,
        conversation_log_bus: ConversationLogBus | None = None,
        text_bus: TextEventBus | None = None,
        user_display_name: str = "Usuário",
        agent_name: str = "Jarvis",
        identity_saver=None,
        current_microphone: str | None = None,
        microphone_saver=None,
        on_command=None,
    ) -> None:
        super().__init__()
        self.state_bus = state_bus
        self.debug_mode = debug_mode
        self.telemetry_bus = telemetry_bus
        self.control_center_bus = control_center_bus
        self.audio_activity_bus = audio_activity_bus
        self.conversation_log_bus = conversation_log_bus
        self.on_command = on_command
        self.debug_controller = (
            AmbientUIDebugController(state_bus) if debug_mode else None
        )
        self._expanded = False
        self._quitting = False
        self.tray: QSystemTrayIcon | None = None

        self.setWindowTitle("Jarvis Ambient HUD")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
        )

        self.hud = AmbientHUDWidget(self)
        # Compatibilidade interna com código/testes que ainda chamem o widget
        # principal de "orb" durante a migração de nomenclatura.
        self.orb = self.hud
        self.control_center_window = ControlCenterWindow(
            user_display_name=user_display_name,
            agent_name=agent_name,
            identity_saver=identity_saver,
            current_microphone=current_microphone,
            microphone_saver=microphone_saver,
            on_command=self.on_command,
        )
        self.hud.clicked.connect(self.show_control_center)
        self.panel = self._build_panel()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(
            WINDOW_MARGIN, WINDOW_MARGIN, WINDOW_MARGIN, WINDOW_MARGIN
        )
        layout.setSpacing(WINDOW_SPACING)
        layout.addWidget(self.hud, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.panel)
        self.panel.hide()
        self.setFixedSize(collapsed_window_size(), collapsed_window_size())

        # O tray precisa existir antes do subscribe: AgentStateBus.subscribe()
        # pode reproduzir o snapshot atual imediatamente.
        self.tray = self._build_tray()
        if self.tray is not None:
            self.tray.show()

        self._relay = _StateRelay(self)
        self._relay.snapshot_received.connect(self._apply_snapshot)
        self._unsubscribe = self.state_bus.subscribe(self._relay.relay)
        self._telemetry_relay = _TelemetryRelay(self)
        self._telemetry_relay.snapshot_received.connect(self._apply_telemetry)
        self._unsubscribe_telemetry = (
            self.telemetry_bus.subscribe(self._telemetry_relay.relay)
            if self.telemetry_bus is not None
            else lambda: None
        )
        self._audio_activity_relay = _AudioActivityRelay(self)
        self._audio_activity_relay.snapshot_received.connect(
            self._apply_audio_activity
        )
        self._unsubscribe_audio_activity = (
            self.audio_activity_bus.subscribe(
                self._audio_activity_relay.relay
            )
            if self.audio_activity_bus is not None
            else lambda: None
        )
        self._text_relay = _ConversationLogRelay(self)
        self._text_relay.snapshot_received.connect(self.control_center_window.apply_text_event)
        self._unsubscribe_text = (text_bus.subscribe(self._text_relay.relay)
                                  if text_bus is not None else lambda: None)
        self._conversation_log_relay = _ConversationLogRelay(self)
        self._conversation_log_relay.snapshot_received.connect(
            self._apply_conversation_log
        )
        self._unsubscribe_conversation_log = (
            self.conversation_log_bus.subscribe(
                self._conversation_log_relay.relay
            )
            if self.conversation_log_bus is not None
            else lambda: None
        )
        self._control_center_relay = _ControlCenterRelay(self)
        self._control_center_relay.snapshot_received.connect(self._apply_control_center)
        self._unsubscribe_control_center = (
            self.control_center_bus.subscribe(self._control_center_relay.relay)
            if self.control_center_bus is not None
            else lambda: None
        )

        self._place_near_work_area()

    def _build_panel(self) -> QFrame:
        return build_ambient_panel(self, HUDPanelFrame)

    def _build_tray(self) -> QSystemTrayIcon | None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return None
        tray = QSystemTrayIcon(self)
        tray.setToolTip("Jarvis Ambient HUD")
        tray.setIcon(self._tray_icon())

        menu = QMenu()
        visibility = QAction("Mostrar / ocultar orb", menu)
        visibility.triggered.connect(self._toggle_visibility)
        menu.addAction(visibility)

        control_center = QAction("Abrir Control Center", menu)
        control_center.triggered.connect(self.show_control_center)
        menu.addAction(control_center)

        if self.debug_mode:
            next_state = QAction("Próximo estado de teste", menu)
            next_state.triggered.connect(self._next_debug_state)
            menu.addAction(next_state)

        menu.addSeparator()
        stop_runtime = QAction("Encerrar JARVIS…", menu)
        stop_runtime.triggered.connect(self.request_runtime_stop)
        menu.addAction(stop_runtime)
        quit_action = QAction("Encerrar interface", menu)
        quit_action.triggered.connect(self.shutdown)
        menu.addAction(quit_action)
        tray.setContextMenu(menu)
        tray.activated.connect(self._tray_activated)
        return tray

    @staticmethod
    def _tray_icon() -> QIcon:
        pixmap = QPixmap(64, 64)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        center = QPointF(32.0, 32.0)

        # Ícone reduzido mantém a linguagem do HUD: arcos + ponto de presença.
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(78, 208, 242, 132), 1.1))
        painter.drawEllipse(QRectF(10.0, 10.0, 44.0, 44.0))
        painter.drawEllipse(QRectF(18.0, 18.0, 28.0, 28.0))
        painter.setPen(QPen(QColor(103, 229, 255, 205), 1.6))
        painter.drawArc(QRectF(12.0, 12.0, 40.0, 40.0), 17 * 16, 72 * 16)
        painter.drawArc(QRectF(12.0, 12.0, 40.0, 40.0), 191 * 16, 48 * 16)
        painter.drawArc(QRectF(19.0, 19.0, 26.0, 26.0), 122 * 16, 68 * 16)

        glow = QRadialGradient(center, 8.0)
        glow.setColorAt(0.0, QColor(244, 255, 255, 255))
        glow.setColorAt(0.25, QColor(82, 224, 255, 225))
        glow.setColorAt(1.0, QColor(82, 224, 255, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(QRectF(24.0, 24.0, 16.0, 16.0))
        painter.end()
        return QIcon(pixmap)

    def _apply_snapshot(self, snapshot: StateSnapshot) -> None:
        self.hud.set_state(snapshot.state)
        self.control_center_window.set_state(snapshot.state)
        self.state_label.setText(label_for(snapshot.state))
        source = snapshot.source.replace("_", " ").upper()
        self.meta_label.setText(f"{source}  //  SEQ {snapshot.sequence:04d}")
        self.setWindowOpacity(profile_for(snapshot.state).standby_opacity)
        if self.tray is not None:
            self.tray.setToolTip(f"Jarvis · {label_for(snapshot.state)}")

    def _apply_telemetry(self, snapshot: TelemetrySnapshot) -> None:
        display = format_telemetry(snapshot)
        self.channels_label.setText(display.channels)
        self.telemetry_health_label.setText(display.health)
        self.control_center_window.set_telemetry(snapshot)

    def _apply_audio_activity(
        self,
        snapshot: AudioActivitySnapshot,
    ) -> None:
        self.hud.set_audio_activity(snapshot.level)
        self.control_center_window.set_audio_activity(snapshot.level)

    def _apply_conversation_log(
        self,
        snapshot: ConversationLogSnapshot,
    ) -> None:
        self.control_center_window.set_conversation_log(snapshot)

    def _apply_control_center(self, snapshot: ControlCenterSnapshot) -> None:
        self.control_center_window.set_control_center(snapshot)
        # Update telemetry tab
        display = format_telemetry(snapshot.telemetry)
        self.channels_label.setText(display.channels)
        self.telemetry_health_label.setText(display.health)

        # Update memory tab
        mem = snapshot.memory
        self.memory_summary_label.setText(
            f"Memória: {mem.total} | Consentimento: {mem.with_consent} | Expirados: {mem.expired} | Sensibilidade: {mem.by_sensitivity}"
        )
        lines = []
        for mem_entry in mem.recent:
            sens = mem_entry.sensitivity.value
            consent = "SIM" if mem_entry.consent_given else "NÃO"
            ret = f"{mem_entry.retention_days}d" if mem_entry.retention_days else "∞"
            lines.append(f"[{mem_entry.category}] {mem_entry.content[:60]}... | {sens} | {consent} | ret={ret} | {mem_entry.id}")
        self.memory_list.setPlainText("\n".join(lines) if lines else "(vazio)")

        # Update skills tab
        sk = snapshot.skills
        self.skills_summary_label.setText(
            f"Skills: {sk.total} | Execuções: {sk.total_executions} | Sucessos: {sk.total_successes}"
        )
        lines = []
        for skill in sk.recent:
            triggers = ", ".join(skill.triggers) if skill.triggers else "(sem triggers)"
            lines.append(f"{skill.name} | exec={skill.execution_count} ok={skill.success_count} | {triggers} | {skill.id}")
        self.skills_list.setPlainText("\n".join(lines) if lines else "(vazio)")

        # Update vision tab
        vis = snapshot.vision
        if vis.enabled:
            self.vision_status_label.setText(
                f"Visão: Habilitada | Última observação: {vis.last_observation or '—'}"
            )
        else:
            self.vision_status_label.setText("Visão: Desabilitada")

    def show_control_center(self) -> None:
        was_visible = self.control_center_window.isVisible()
        screen = QApplication.primaryScreen()
        if screen is not None and not was_visible:
            self.control_center_window.move(
                screen.availableGeometry().center()
                - self.control_center_window.rect().center()
            )
        self.control_center_window.showNormal()
        self.control_center_window.raise_()
        self.control_center_window.activateWindow()

    def toggle_control_center(self) -> None:
        if self.control_center_window.isVisible():
            self.control_center_window.hide()
        else:
            self.show_control_center()

    def toggle_panel(self) -> None:
        # Compatibilidade com chamadas antigas: o clique agora abre o
        # Control Center completo em vez de transformar o orb em mini-dashboard.
        self.toggle_control_center()

    def _apply_selected_debug_state(self) -> None:
        controller = self.debug_controller
        if controller is None:
            return
        value = self.state_selector.currentData()
        controller.set_state(AgentState(value))

    def _next_debug_state(self) -> None:
        controller = self.debug_controller
        if controller is None:
            return
        state = controller.next_state()
        index = self.state_selector.findData(state.value)
        if index >= 0:
            self.state_selector.setCurrentIndex(index)

    def _toggle_visibility(self) -> None:
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.raise_()
            self.activateWindow()

    def _tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in {
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        }:
            self._toggle_visibility()

    def _place_near_work_area(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        geometry = screen.availableGeometry()
        self.move(
            geometry.right() - self.width() - 24,
            geometry.bottom() - self.height() - 24,
        )

    def request_runtime_stop(self) -> None:
        self.show_control_center()
        self.control_center_window.runtime_stop.prepare()

    def shutdown(self) -> None:
        self._quitting = True
        self._unsubscribe()
        self._unsubscribe_telemetry()
        self._unsubscribe_audio_activity()
        self._unsubscribe_text()
        self._unsubscribe_conversation_log()
        self._unsubscribe_control_center()
        if self.tray is not None:
            self.tray.hide()
        self.control_center_window.close()
        self.close()
        QApplication.quit()

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self._quitting and self.tray is not None:
            event.ignore()
            self.hide()
            return
        self._unsubscribe()
        self._unsubscribe_telemetry()
        self._unsubscribe_audio_activity()
        self._unsubscribe_text()
        self._unsubscribe_conversation_log()
        self._unsubscribe_control_center()
        self.control_center_window.close()
        event.accept()
        if self.tray is None:
            QApplication.quit()


def run_qt_ambient_ui(
    *,
    state_bus: AgentStateBus,
    debug_mode: bool = False,
    telemetry_bus: TelemetryBus | None = None,
    control_center_bus: ControlCenterBus | None = None,
    audio_activity_bus: AudioActivityBus | None = None,
    conversation_log_bus: ConversationLogBus | None = None,
    text_bus: TextEventBus | None = None,
    open_control_center: bool = False,
    user_display_name: str = "Usuário",
    agent_name: str = "Jarvis",
    identity_saver=None,
    current_microphone: str | None = None,
    microphone_saver=None,
    on_command=None,
) -> int:
    """Inicializa a janela Qt consumindo somente o barramento de aplicação."""

    existing_app = QApplication.instance()
    owns_app = existing_app is None
    app = existing_app or QApplication([])
    app.setApplicationName("Jarvis")
    if getattr(sys, 'frozen', False):
        from PySide6.QtGui import QIcon
        app.setWindowIcon(QIcon(str(Path(sys._MEIPASS) / 'assets/jarvis.svg')))
    app.setQuitOnLastWindowClosed(False)

    window = AmbientWindow(
        state_bus,
        telemetry_bus=telemetry_bus,
        control_center_bus=control_center_bus,
        audio_activity_bus=audio_activity_bus,
        conversation_log_bus=conversation_log_bus,
        text_bus=text_bus,
        debug_mode=debug_mode,
        user_display_name=user_display_name,
        agent_name=agent_name,
        identity_saver=identity_saver,
        current_microphone=current_microphone,
        microphone_saver=microphone_saver,
        on_command=on_command,
    )
    # Keep top-level widgets alive when the caller already owns QApplication.
    app._jarvis_ambient_window = window
    if getattr(sys, 'frozen', False) and os.environ.get('JARVIS_PACKAGED_DIAGNOSTIC') == '1':
        def diagnostic_when_connected():
            page = window.control_center_window.help_page
            if page.client.generation and not page.busy:
                window.control_center_window._show_page('Ajuda')
                QTimer.singleShot(100, page.diagnose)
            else:
                QTimer.singleShot(250, diagnostic_when_connected)
        QTimer.singleShot(500, diagnostic_when_connected)
    window.show()
    if open_control_center:
        # No Windows, a segunda top-level precisa esperar o primeiro frame da
        # Ambient HUD para ganhar handle/visibilidade de forma confiável.
        QTimer.singleShot(300, window.show_control_center)

    if not owns_app:
        return 0
    return app.exec()
