"""App-local petroleum/mint appearance; never changes OS appearance."""
import re
import weakref

from PySide6.QtCore import QObject, QEvent, Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QPalette
from PySide6.QtWidgets import QWidget


SHELL_STYLE = '''
QWidget#controlCenter { background: #0b1720; color: #e7f0f5; }
QFrame#sidebar, QWidget#navigation { background: #142733; border: none; }
QPushButton#nav, QPushButton#navActive { font-size: 14px; padding: 12px 10px; border-radius: 9px; }
QPushButton#navActive { background: #203e39; color: #e7f0f5; border: none; }
QPushButton#nav { color: #abc0cd; }
QPushButton#nav:hover { background: #203641; color: #e7f0f5; }
QPushButton#primaryAction { background: #86e0cb; color: #102d28; border-color: #86e0cb; }
QLabel#heroTitle { font-size: 27px; font-weight: 600; color: #e7f0f5; }
QLabel#heroSub, QLabel#brandTagline, QLabel#cardSecondary { color: #abc0cd; font-size: 13px; }
QLabel#sectionEyebrow, QLabel#footer { color: #abc0cd; font-size: 12px; }
QLabel#conversationMessage { color: #e7f0f5; font-size: 15px; }
QFrame#conversationPanel { background: #0b1720; border: none; }
QFrame#assistantBubble { background: #142733; border: 1px solid #2d4655; border-radius: 14px; }
QFrame#userBubble { background: #203641; border: 1px solid #2d4655; border-radius: 14px; }
QFrame#detailPanel, QFrame#personalCard { background: #142733; border: 1px solid #2d4655; border-radius: 12px; }
QLabel#pageTitle { color: #e7f0f5; font-size: 28px; font-weight: 600; }
QLabel#sectionTitle { color: #e7f0f5; font-size: 20px; font-weight: 600; }
'''


def light_stylesheet(css):
    def declaration(match):
        prop, value = match.group(1), match.group(2)
        def color(token):
            raw = token.group()
            q = QColor(raw) if raw.startswith('#') else None
            alpha = 1.0
            if q is None:
                parts = [float(n) for n in re.findall(r'[\d.]+', raw)]
                q = QColor(*[int(n) for n in parts[:3]])
                if len(parts) == 4:
                    alpha = parts[3] / 255 if parts[3] > 1 else parts[3]
            r, g, b = q.red(), q.green(), q.blue()
            if prop.startswith('border'):
                return '#d4e1e7'
            if prop == 'selection-background-color':
                return '#d9eee7'
            if prop == 'selection-color':
                return '#172c37'
            if prop in {'background', 'background-color', 'alternate-background-color'}:
                if raw.casefold() == '#203e39':
                    return '#d9eee7'
                if max(r, g, b) > 140 and g > r * 1.25 and g > b * .8 and alpha > .5:
                    return '#076e60'
                return '#e5f0ee' if alpha < .5 else '#f3f7f8' if max(r, g, b) < 38 else '#ffffff'
            if (r, g, b) in {(9, 33, 38), (16, 45, 40)}:
                return '#ffffff'
            if r > g * 1.25 and g > b * 1.15:
                return '#83500b'
            if r > g * 1.5:
                return '#a43131'
            if g > r * 1.35 and g > b * .9:
                return '#086b59'
            return '#536b78' if max(r, g, b) < 205 or alpha < .8 else '#172c37'
        value = re.sub(r'#[0-9a-fA-F]{6}\b|rgba?\([\d.,\s]+\)', color, value)
        return prop + ': ' + value
    return re.sub(r'(background(?:-color)?|alternate-background-color|selection-background-color|selection-color|color|border(?:-color)?)\s*:\s*([^;{}]+)', declaration, css)


class DesktopTheme(QObject):
    def __init__(self, root):
        super().__init__(root)
        self.root = root
        self.theme, self.density = 'dark', 'comfortable'
        self.original = weakref.WeakKeyDictionary()
        self.applied = weakref.WeakKeyDictionary()
        self.applying = False
        self.pending = QTimer(self)
        self.pending.setSingleShot(True)
        self.pending.timeout.connect(lambda: self.apply(self.theme, self.density))
        self.watch(root)
        hints = QGuiApplication.styleHints()
        if hasattr(hints, 'colorSchemeChanged'):
            hints.colorSchemeChanged.connect(lambda _scheme: self.apply(self.theme, self.density))

    def watch(self, widget):
        if widget not in self.original:
            self.original[widget] = widget.styleSheet()
            widget.installEventFilter(self)
        for child in widget.findChildren(QWidget):
            if child not in self.original:
                self.original[child] = child.styleSheet()
                child.installEventFilter(self)

    def is_light(self):
        return self.theme == 'light' or self.theme == 'system' and QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Light

    def apply(self, theme, density):
        self.theme, self.density = theme, density
        self.watch(self.root)
        self.applying = True
        try:
            palette = QPalette(self.root.palette())
            light = self.is_light()
            for role, dark, day in [(QPalette.ColorRole.Window, '#0b1720', '#f3f7f8'),
                (QPalette.ColorRole.Base, '#142733', '#ffffff'), (QPalette.ColorRole.Text, '#e7f0f5', '#172c37'),
                (QPalette.ColorRole.WindowText, '#e7f0f5', '#172c37'), (QPalette.ColorRole.ButtonText, '#e7f0f5', '#172c37'),
                (QPalette.ColorRole.Button, '#203641', '#e5f0ee'), (QPalette.ColorRole.PlaceholderText, '#abc0cd', '#536b78')]:
                palette.setColor(role, QColor(day if light else dark))
            self.root.setPalette(palette)
            for widget, original in list(self.original.items()):
                from shiboken6 import isValid
                if not isValid(widget):
                    continue
                if not original:
                    continue
                css = original
                if widget is self.root:
                    css += SHELL_STYLE
                    css += ' QWidget { font-size: ' + ('14px' if density == 'comfortable' else '13px') + '; }'
                css = light_stylesheet(css) if light else css
                if widget.styleSheet() != css:
                    widget.setStyleSheet(css)
                self.applied[widget] = css
        finally:
            self.applying = False

    def eventFilter(self, watched, event):
        if not self.applying and event.type() == QEvent.Type.ChildAdded and isinstance(event.child(), QWidget):
            child = event.child()
            self.watch(child)
            self.pending.start(0)
        elif not self.applying and event.type() == QEvent.Type.StyleChange and isinstance(watched, QWidget):
            value = watched.styleSheet()
            if value and value != self.applied.get(watched):
                self.original[watched] = value
                self.pending.start(0)
        return False
