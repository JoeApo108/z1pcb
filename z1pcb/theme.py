"""Colours and style sheet of the app (dark, Fusion based: looks the same on Windows and macOS)."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPalette, QPixmap

C = {
    'bg': '#1d2025',
    'panel': '#24282e',
    'field': '#2c3138',
    'border': '#363c44',
    'text': '#e3e6ea',
    'muted': '#8b939c',
    'accent': '#3b82f6',
    'accent_hi': '#5b9bff',
    'ok': '#22c55e',
    'bad': '#ef4444',
    'warn': '#f59e0b',
    'canvas': '#15171b',
}
LAYER = {
    'board': QColor('#4b525b'),
    'copper': QColor('#b98a3e'),
    4: QColor('#3b82f6'),
    2: QColor('#22d3ee'),
    3: QColor('#f59e0b'),
    5: QColor('#c084fc'),
    'problem': QColor('#ef4444'),
}
BANNER = {  # state -> (background, text)
    'ok': ('#14532d', '#bbf7d0'),
    'warn': ('#713f12', '#fde68a'),
    'bad': ('#7f1d1d', '#fecaca'),
    'busy': ('#1e3a5f', '#bfdbfe'),
    'idle': (C['panel'], C['muted']),
}

QSS = (
    """
QWidget { color: %(text)s; }
QMainWindow, #Sidebar, #ResultPane { background: %(bg)s; }
#Sidebar { border-right: 1px solid %(border)s; }
QLabel#Section { color: %(muted)s; font-size: 11px; font-weight: 600; letter-spacing: 1px; padding-top: 14px; }
QLabel#Hint { color: %(muted)s; font-size: 11px; }
QLabel#FileName { color: %(text)s; }
QLabel#FileMissing { color: %(warn)s; }
QFrame#Card { background: %(panel)s; border: 1px solid %(border)s; border-radius: 6px; }
QLineEdit, QAbstractSpinBox, QComboBox {
    background: %(field)s; border: 1px solid %(border)s; border-radius: 4px; padding: 5px 8px; }
QLineEdit:focus, QAbstractSpinBox:focus, QComboBox:focus { border-color: %(accent)s; }
QAbstractSpinBox:disabled { color: %(muted)s; background: %(panel)s; }
QAbstractSpinBox QLineEdit { background: transparent; border: none; padding: 0; margin: 0; }
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {
    subcontrol-origin: border; width: 0px; height: 0px; border: none; }
QPushButton { background: %(field)s; border: 1px solid %(border)s; border-radius: 5px; padding: 6px 12px; }
QPushButton:hover { border-color: %(accent)s; }
QPushButton:disabled { color: %(muted)s; background: %(panel)s; }
QPushButton#Primary {
    background: %(accent)s; border: 1px solid %(accent)s; color: white; font-weight: 600; padding: 8px 12px; }
QPushButton#Primary:hover { background: %(accent_hi)s; }
QPushButton#Primary:disabled { background: %(border)s; border-color: %(border)s; color: %(muted)s; }
QToolButton { background: transparent; border: 1px solid transparent; border-radius: 4px; padding: 3px 8px; }
QToolButton:hover { border-color: %(border)s; background: %(field)s; }
QToolButton:checked { background: %(field)s; border-color: %(border)s; }
QToolButton#Seg { border: 1px solid %(border)s; padding: 5px 10px; }
QToolButton#Seg:checked { background: %(accent)s; border-color: %(accent)s; color: white; }
QToolButton#Layer { padding: 4px 8px; }
QToolButton#Layer:checked { background: transparent; border-color: transparent; }
QToolButton#Layer:checked:hover { background: %(field)s; border-color: %(border)s; }
QToolButton#Link { color: %(accent_hi)s; padding: 2px 0; }
#ActionBar { background: %(bg)s; border-top: 1px solid %(border)s; border-right: 1px solid %(border)s; }
QCheckBox { spacing: 6px; }
QTableWidget { background: %(panel)s; border: 1px solid %(border)s; border-radius: 6px; gridline-color: transparent; }
QTableWidget::item { padding: 4px 8px; border-bottom: 1px solid %(border)s; }
QHeaderView::section {
    background: %(panel)s; color: %(muted)s; border: none; border-bottom: 1px solid %(border)s;
    padding: 6px 8px; font-weight: 600; }
QStatusBar { background: %(panel)s; border-top: 1px solid %(border)s; color: %(muted)s; }
QStatusBar QLabel { color: %(muted)s; padding: 0 6px; }
QProgressBar {
    background: %(field)s; border: 1px solid %(border)s; border-radius: 4px; height: 10px;
    text-align: center; color: transparent; }
QProgressBar::chunk { background: %(accent)s; border-radius: 3px; }
QSplitter::handle { background: %(border)s; }
QScrollArea { border: none; background: %(bg)s; }
#Banner { border-radius: 6px; padding: 10px 14px; font-weight: 600; }
"""
    % C
)


def apply_theme(app):
    app.setStyle('Fusion')
    pal = QPalette()
    roles = (
        (QPalette.Window, C['bg']),
        (QPalette.Base, C['field']),
        (QPalette.AlternateBase, C['panel']),
        (QPalette.Text, C['text']),
        (QPalette.WindowText, C['text']),
        (QPalette.Button, C['field']),
        (QPalette.ButtonText, C['text']),
        (QPalette.Highlight, C['accent']),
        (QPalette.HighlightedText, '#ffffff'),
        (QPalette.ToolTipBase, C['panel']),
        (QPalette.ToolTipText, C['text']),
        (QPalette.PlaceholderText, C['muted']),
    )
    for role, col in roles:
        pal.setColor(role, QColor(col))
    pal.setColor(QPalette.Disabled, QPalette.Text, QColor(C['muted']))
    pal.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(C['muted']))
    app.setPalette(pal)
    app.setStyleSheet(QSS)


def swatch(color, size=10):
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(color)
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(0, 0, size, size, 2, 2)
    p.end()
    return QIcon(pm)
