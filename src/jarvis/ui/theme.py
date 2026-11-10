"""Shared desktop controls; no system theme or preferences are changed."""

CONTROL_STYLE = """
QWidget { color: #dce6f0; font-family: 'Segoe UI'; font-size: 13px; }
QPushButton { background: #172635; border: 1px solid #304354;
    border-radius: 8px; padding: 8px 12px; color: #dce6f0; }
QPushButton:hover { background: #223749; border-color: #56859b; }
QPushButton:pressed, QPushButton:checked { background: #164759; border-color: #55c8d8; }
QPushButton:focus, QLineEdit:focus, QComboBox:focus { border: 1px solid #6ad5e0; }
QPushButton:disabled { color: #718495; background: #111e2a; border-color: #263745; }
QPushButton#primaryAction { background: #69d6d4; color: #092126; font-weight: 600;
    border: 1px solid #69d6d4; }
QPushButton#primaryAction:hover { background: #92e5df; }
QPushButton#primaryAction:disabled { background: #243c45; color: #82989e; border-color: #304954; }
QLineEdit, QComboBox, QSpinBox, QDateTimeEdit, QPlainTextEdit, QListWidget {
    background: #0e1b27; border: 1px solid #304354; border-radius: 8px;
    padding: 7px; color: #dce6f0; selection-background-color: #24586b; }
QComboBox::drop-down { border: 0; width: 24px; }
QComboBox QAbstractItemView { background: #172635; color: #dce6f0; selection-background-color: #24586b; }
QCheckBox { spacing: 8px; padding: 4px; }
QTabWidget::pane { background: #0e1b27; border: 1px solid #304354; border-radius: 8px; }
QTabBar::tab { background: #152431; color: #9eafbf; padding: 10px 16px; }
QTabBar::tab:selected { background: #223c4c; color: #c2f4f0; }
QScrollArea { border: 0; background: transparent; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: #3a5265; border-radius: 3px; min-height: 26px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QToolTip { background: #213849; color: #e9f4fa; border: 1px solid #47677d; padding: 5px; }
"""
