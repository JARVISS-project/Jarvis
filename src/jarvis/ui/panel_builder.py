from __future__ import annotations

from typing import Any, Callable

from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from jarvis.application.telemetry import TelemetrySnapshot
from jarvis.ui.debug import AmbientUIDebugController
from jarvis.ui.hud_model import label_for
from jarvis.ui.layout import PANEL_WIDTH
from jarvis.ui.telemetry_model import format_telemetry


def build_ambient_panel(
    owner: Any,
    frame_factory: Callable[[QWidget], QFrame],
) -> QFrame:
    panel = frame_factory(owner)
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
    owner.state_label = QLabel("STANDBY")
    owner.state_label.setObjectName("state")
    owner.meta_label = QLabel("STARTUP  //  SEQ 0000")
    owner.meta_label.setObjectName("meta")
    
    separator = QFrame(panel)
    separator.setObjectName("separator")
    
    display = format_telemetry(TelemetrySnapshot())
    owner.channels_label = QLabel(display.channels)
    owner.channels_label.setObjectName("channel")
    owner.telemetry_health_label = QLabel(display.health)
    owner.telemetry_health_label.setObjectName("online")
    
    layout.addWidget(eyebrow)
    layout.addWidget(title)
    layout.addSpacing(8)
    layout.addWidget(owner.state_label)
    layout.addWidget(owner.meta_label)
    layout.addSpacing(6)
    layout.addWidget(separator)
    layout.addSpacing(4)
    layout.addWidget(owner.channels_label)
    layout.addWidget(owner.telemetry_health_label)
    layout.addSpacing(8)
    
    # Control Center tabs
    owner.control_tabs = QTabWidget(panel)
    owner.control_tabs.setObjectName("controlTabs")
    owner.control_tabs.setStyleSheet(
        """
        QTabWidget::pane {
            border: 1px solid rgba(71, 192, 226, 52);
            background: rgba(2, 8, 13, 200);
            border-radius: 2px;
        }
        QTabBar::tab {
            color: rgba(191, 230, 240, 220);
            background: rgba(4, 20, 29, 210);
            border: 1px solid rgba(68, 175, 207, 62);
            border-bottom: none;
            border-top-left-radius: 2px;
            border-top-right-radius: 2px;
            padding: 4px 12px;
            margin-right: 2px;
            font-size: 8px;
            letter-spacing: 1px;
        }
        QTabBar::tab:selected {
            color: rgba(232, 251, 255, 248);
            background: rgba(2, 8, 13, 200);
            border-bottom: 1px solid rgba(2, 8, 13, 200);
        }
        QTabBar::tab:hover:!selected {
            background: rgba(7, 35, 48, 225);
        }
        """
    )
    
    # Telemetry tab
    owner.telemetry_tab = QWidget()
    telemetry_layout = QVBoxLayout(owner.telemetry_tab)
    telemetry_layout.setContentsMargins(12, 10, 12, 10)
    telemetry_layout.setSpacing(6)
    
    owner.channels_label = QLabel(display.channels)
    owner.channels_label.setObjectName("channel")
    telemetry_layout.addWidget(owner.channels_label)
    
    owner.telemetry_health_label = QLabel(display.health)
    owner.telemetry_health_label.setObjectName("online")
    telemetry_layout.addWidget(owner.telemetry_health_label)
    
    telemetry_layout.addStretch(1)
    owner.control_tabs.addTab(owner.telemetry_tab, "TELEMETRY")
    
    # Memory tab
    owner.memory_tab = QWidget()
    memory_layout = QVBoxLayout(owner.memory_tab)
    memory_layout.setContentsMargins(12, 10, 12, 10)
    memory_layout.setSpacing(6)
    
    owner.memory_summary_label = QLabel("Memória: 0 | Consentimento: 0 | Expirados: 0")
    owner.memory_summary_label.setObjectName("meta")
    owner.memory_summary_label.setWordWrap(True)
    memory_layout.addWidget(owner.memory_summary_label)
    
    owner.memory_list = QTextEdit()
    owner.memory_list.setReadOnly(True)
    owner.memory_list.setMaximumHeight(160)
    owner.memory_list.setStyleSheet(
        """
        QTextEdit {
            color: rgba(213, 239, 247, 230);
            background: rgba(2, 8, 13, 180);
            border: 1px solid rgba(71, 192, 226, 40);
            border-radius: 1px;
            font-family: "Consolas", "Segoe UI";
            font-size: 8px;
        }
        """
    )
    memory_layout.addWidget(owner.memory_list)
    
    owner.control_tabs.addTab(owner.memory_tab, "MEMORY")
    
    # Skills tab
    owner.skills_tab = QWidget()
    skills_layout = QVBoxLayout(owner.skills_tab)
    skills_layout.setContentsMargins(12, 10, 12, 10)
    skills_layout.setSpacing(6)
    
    owner.skills_summary_label = QLabel("Skills: 0 | Execuções: 0 | Sucessos: 0")
    owner.skills_summary_label.setObjectName("meta")
    skills_layout.addWidget(owner.skills_summary_label)
    
    owner.skills_list = QTextEdit()
    owner.skills_list.setReadOnly(True)
    owner.skills_list.setMaximumHeight(160)
    owner.skills_list.setStyleSheet(
        """
        QTextEdit {
            color: rgba(213, 239, 247, 230);
            background: rgba(2, 8, 13, 180);
            border: 1px solid rgba(71, 192, 226, 40);
            border-radius: 1px;
            font-family: "Consolas", "Segoe UI";
            font-size: 8px;
        }
        """
    )
    skills_layout.addWidget(owner.skills_list)
    
    owner.control_tabs.addTab(owner.skills_tab, "SKILLS")
    
    # Vision tab
    owner.vision_tab = QWidget()
    vision_layout = QVBoxLayout(owner.vision_tab)
    vision_layout.setContentsMargins(12, 10, 12, 10)
    vision_layout.setSpacing(6)
    
    owner.vision_status_label = QLabel("Visão: Desabilitada")
    owner.vision_status_label.setObjectName("meta")
    vision_layout.addWidget(owner.vision_status_label)
    
    vision_layout.addStretch(1)
    owner.control_tabs.addTab(owner.vision_tab, "VISION")
    
    layout.addWidget(owner.control_tabs)
    layout.addStretch(1)
    
    owner.state_selector = QComboBox(panel)
    for state in AmbientUIDebugController.DEFAULT_SEQUENCE:
        owner.state_selector.addItem(label_for(state), state.value)
    owner.state_selector.setVisible(owner.debug_mode)
    layout.addWidget(owner.state_selector)
    
    buttons = QHBoxLayout()
    buttons.setSpacing(5)
    owner.apply_button = QPushButton("SET")
    owner.next_button = QPushButton("NEXT")
    owner.apply_button.setVisible(owner.debug_mode)
    owner.next_button.setVisible(owner.debug_mode)
    owner.apply_button.clicked.connect(owner._apply_selected_debug_state)
    owner.next_button.clicked.connect(owner._next_debug_state)
    buttons.addWidget(owner.apply_button)
    buttons.addWidget(owner.next_button)
    layout.addLayout(buttons)
    
    mode_label = QLabel(
        "SIMULATION BUS // NO DEVICE INPUT"
        if owner.debug_mode
        else "RUNTIME BUS // READ ONLY"
    )
    mode_label.setObjectName("meta")
    layout.addWidget(mode_label)
    
    outer.addWidget(content)
    return panel
