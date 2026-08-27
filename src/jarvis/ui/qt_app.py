"""Implementação Qt da Ambient HUD.

Este módulo é importado somente pelo launcher da UI. O Core não importa PySide6.
A linguagem visual usa geometria e animação próprias, sem assets de terceiros.
"""

from __future__ import annotations

import math

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
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from jarvis.application.state import AgentState, AgentStateBus, StateSnapshot
from jarvis.ui.debug import AmbientUIDebugController
from jarvis.ui.hud_model import HUDProfile, label_for, profile_for
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


# Azul/ciano permanece a identidade principal. Tons de atenção aparecem somente
# nos estados que realmente precisam comunicar confirmação ou erro.
_STATE_COLORS = {
    AgentState.WAITING_WAKE: ((13, 55, 75), (57, 185, 220)),
    AgentState.LISTENING: ((5, 92, 119), (85, 232, 255)),
    AgentState.PROCESSING: ((12, 72, 132), (87, 176, 255)),
    AgentState.SPEAKING: ((6, 105, 126), (111, 240, 255)),
    AgentState.WAITING_FOLLOWUP: ((13, 79, 111), (89, 201, 235)),
    AgentState.WAITING_REPAIR: ((19, 69, 117), (112, 183, 239)),
    AgentState.CONFIRMATION: ((104, 77, 20), (255, 205, 94)),
    AgentState.EXECUTING: ((7, 83, 130), (69, 211, 255)),
    AgentState.ERROR: ((112, 28, 39), (255, 91, 105)),
}


class _StateRelay(QObject):
    snapshot_received = Signal(object)

    def relay(self, snapshot: StateSnapshot) -> None:
        self.snapshot_received.emit(snapshot)


class AmbientHUDWidget(QWidget):
    """HUD circular ambient desenhada por código, sem logotipo central."""

    clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(HUD_WIDGET_SIZE, HUD_WIDGET_SIZE)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._state = AgentState.WAITING_WAKE
        self._primary_phase = 0.0
        self._secondary_phase = 0.0
        self._pulse_phase = 0.0
        self._press_position: QPoint | None = None
        self._last_drag_position: QPoint | None = None
        self._dragged = False

        self._animation = QTimer(self)
        self._animation.setInterval(30)
        self._animation.timeout.connect(self._advance_animation)
        self._animation.start()

    def set_state(self, state: AgentState) -> None:
        self._state = state
        self.update()

    def _advance_animation(self) -> None:
        profile = profile_for(self._state)
        self._primary_phase = (self._primary_phase + profile.primary_speed) % (
            math.pi * 2.0
        )
        self._secondary_phase = (
            self._secondary_phase + profile.secondary_speed
        ) % (math.pi * 2.0)
        self._pulse_phase = (self._pulse_phase + profile.pulse_speed) % (
            math.pi * 2.0
        )
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
            "CORE // SAFE",
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

    def paintEvent(self, _event: object) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        scale = min(self.width(), self.height()) / float(HUD_DESIGN_SIZE)
        painter.scale(scale, scale)

        inner_rgb, accent_rgb = _STATE_COLORS[self._state]
        profile = profile_for(self._state)
        center = QPointF(HUD_DESIGN_SIZE / 2.0, HUD_DESIGN_SIZE / 2.0)
        pulse = (math.sin(self._pulse_phase) + 1.0) / 2.0

        self._draw_ambient_field(
            painter,
            center,
            inner_rgb,
            accent_rgb,
            pulse,
            profile,
        )
        self._draw_reference_geometry(painter, center, accent_rgb, profile)
        self._draw_ticks(painter, center, accent_rgb, profile)
        self._draw_state_overlay(painter, center, accent_rgb, pulse)
        self._draw_arc_layers(painter, center, accent_rgb, pulse, profile)
        self._draw_scanner(painter, center, accent_rgb, pulse, profile)
        self._draw_waveform(painter, center, accent_rgb, profile)
        self._draw_data_nodes(painter, center, accent_rgb, profile)
        self._draw_center_presence(painter, center, accent_rgb, pulse, profile)
        self._draw_micro_telemetry(painter, center, accent_rgb, profile)
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


class AmbientWindow(QWidget):
    """Janela flutuante da Ambient HUD com painel e system tray."""

    def __init__(
        self,
        state_bus: AgentStateBus,
        *,
        debug_mode: bool,
    ) -> None:
        super().__init__()
        self.state_bus = state_bus
        self.debug_mode = debug_mode
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
        self.hud.clicked.connect(self.toggle_panel)
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

        self._place_near_work_area()

    def _build_panel(self) -> QFrame:
        panel = HUDPanelFrame(self)
        panel.setObjectName("ambientPanel")
        panel.setFixedWidth(PANEL_WIDTH)
        panel.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        panel.setStyleSheet(
            """
            QFrame#ambientPanel {
                background: rgba(2, 8, 13, 232);
                border: 1px solid rgba(71, 192, 226, 52);
                border-radius: 2px;
            }
            QFrame#statusRail {
                background: rgba(79, 218, 255, 150);
                border: 0;
            }
            QFrame#separator {
                background: rgba(78, 192, 224, 46);
                border: 0;
                min-height: 1px;
                max-height: 1px;
            }
            QLabel {
                color: rgba(213, 239, 247, 230);
                font-family: "Segoe UI";
            }
            QLabel#eyebrow {
                color: rgba(91, 192, 222, 176);
                font-size: 8px;
                font-weight: 600;
                letter-spacing: 2px;
            }
            QLabel#title {
                color: rgba(190, 240, 252, 244);
                font-size: 13px;
                font-weight: 650;
                letter-spacing: 4px;
            }
            QLabel#state {
                color: rgba(232, 251, 255, 248);
                font-size: 18px;
                font-weight: 300;
                letter-spacing: 2px;
            }
            QLabel#meta {
                color: rgba(119, 168, 183, 180);
                font-size: 9px;
                letter-spacing: 1px;
            }
            QLabel#channel {
                color: rgba(74, 180, 211, 178);
                font-size: 8px;
                letter-spacing: 1px;
            }
            QLabel#online {
                color: rgba(132, 225, 244, 205);
                font-size: 9px;
                letter-spacing: 1px;
            }
            QComboBox, QPushButton {
                color: rgba(191, 230, 240, 220);
                background: rgba(4, 20, 29, 210);
                border: 1px solid rgba(68, 175, 207, 62);
                border-radius: 1px;
                padding: 5px 7px;
                min-height: 18px;
                font-size: 9px;
                letter-spacing: 1px;
            }
            QComboBox:hover, QPushButton:hover {
                background: rgba(7, 35, 48, 225);
                border-color: rgba(83, 211, 244, 120);
            }
            """
        )

        outer = QHBoxLayout(panel)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        rail = QFrame(panel)
        rail.setObjectName("statusRail")
        rail.setFixedWidth(1)
        outer.addWidget(rail)

        content = QWidget(panel)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(20, 17, 20, 16)
        layout.setSpacing(6)

        eyebrow = QLabel("LOCAL COGNITIVE SYSTEM // AMBIENT CHANNEL")
        eyebrow.setObjectName("eyebrow")
        title = QLabel("JARVIS")
        title.setObjectName("title")
        self.state_label = QLabel("STANDBY")
        self.state_label.setObjectName("state")
        self.meta_label = QLabel("STARTUP  //  SEQ 0000")
        self.meta_label.setObjectName("meta")

        separator = QFrame(panel)
        separator.setObjectName("separator")

        channels = QLabel("VOICE  01   CORE  01   TOOLS  01   SAFETY  01")
        channels.setObjectName("channel")
        online = QLabel("● LOCAL LINK   ● POLICY LAYER   ● AUDIT")
        online.setObjectName("online")

        layout.addWidget(eyebrow)
        layout.addWidget(title)
        layout.addSpacing(8)
        layout.addWidget(self.state_label)
        layout.addWidget(self.meta_label)
        layout.addSpacing(6)
        layout.addWidget(separator)
        layout.addSpacing(4)
        layout.addWidget(channels)
        layout.addWidget(online)
        layout.addStretch(1)

        self.state_selector = QComboBox(panel)
        for state in AmbientUIDebugController.DEFAULT_SEQUENCE:
            self.state_selector.addItem(label_for(state), state.value)
        self.state_selector.setVisible(self.debug_mode)
        layout.addWidget(self.state_selector)

        buttons = QHBoxLayout()
        buttons.setSpacing(5)
        self.apply_button = QPushButton("SET")
        self.next_button = QPushButton("NEXT")
        self.apply_button.setVisible(self.debug_mode)
        self.next_button.setVisible(self.debug_mode)
        self.apply_button.clicked.connect(self._apply_selected_debug_state)
        self.next_button.clicked.connect(self._next_debug_state)
        buttons.addWidget(self.apply_button)
        buttons.addWidget(self.next_button)
        layout.addLayout(buttons)

        mode_label = QLabel(
            "SIMULATION BUS // NO DEVICE INPUT"
            if self.debug_mode
            else "RUNTIME BUS // LIVE"
        )
        mode_label.setObjectName("meta")
        layout.addWidget(mode_label)

        outer.addWidget(content)
        return panel

    def _build_tray(self) -> QSystemTrayIcon | None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return None
        tray = QSystemTrayIcon(self)
        tray.setToolTip("Jarvis Ambient HUD")
        tray.setIcon(self._tray_icon())

        menu = QMenu()
        visibility = QAction("Mostrar / ocultar", menu)
        visibility.triggered.connect(self._toggle_visibility)
        menu.addAction(visibility)

        if self.debug_mode:
            next_state = QAction("Próximo estado de teste", menu)
            next_state.triggered.connect(self._next_debug_state)
            menu.addAction(next_state)

        menu.addSeparator()
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
        self.state_label.setText(label_for(snapshot.state))
        source = snapshot.source.replace("_", " ").upper()
        self.meta_label.setText(f"{source}  //  SEQ {snapshot.sequence:04d}")
        self.setWindowOpacity(profile_for(snapshot.state).standby_opacity)
        if self.tray is not None:
            self.tray.setToolTip(f"Jarvis · {label_for(snapshot.state)}")

    def toggle_panel(self) -> None:
        right_edge = self.geometry().right()
        top = self.y()
        self._expanded = not self._expanded
        self.panel.setVisible(self._expanded)
        if self._expanded:
            self.setFixedSize(
                expanded_window_width(), EXPANDED_WINDOW_HEIGHT
            )
        else:
            self.setFixedSize(
                collapsed_window_size(), collapsed_window_size()
            )
        self.move(right_edge - self.width() + 1, top)

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

    def shutdown(self) -> None:
        self._quitting = True
        self._unsubscribe()
        if self.tray is not None:
            self.tray.hide()
        self.close()
        QApplication.quit()

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self._quitting and self.tray is not None:
            event.ignore()
            self.hide()
            return
        self._unsubscribe()
        event.accept()
        if self.tray is None:
            QApplication.quit()


def run_qt_ambient_ui(
    *,
    state_bus: AgentStateBus,
    debug_mode: bool = False,
) -> int:
    """Inicializa a janela Qt consumindo somente o barramento de aplicação."""

    existing_app = QApplication.instance()
    owns_app = existing_app is None
    app = existing_app or QApplication([])
    app.setApplicationName("Jarvis")
    app.setQuitOnLastWindowClosed(False)

    window = AmbientWindow(state_bus, debug_mode=debug_mode)
    window.show()

    if not owns_app:
        return 0
    return app.exec()
