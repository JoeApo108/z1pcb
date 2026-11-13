"""Zoomable 2D canvas (Y up, mm)."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QFrame, QGraphicsScene, QGraphicsView

CANVAS = '#15171b'


class View(QGraphicsView):
    cursor_mm = Signal(float, float)

    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setRenderHints(QPainter.Antialiasing)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setBackgroundBrush(QColor(CANVAS))
        self.setFrameShape(QFrame.NoFrame)
        self.setMouseTracking(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)  # panning is by dragging
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scale(1, -1)  # Gerber Y up

    def wheelEvent(self, e):
        f = 1.2 if e.angleDelta().y() > 0 else 1 / 1.2
        self.scale(f, f)

    def mouseMoveEvent(self, e):
        p = self.mapToScene(e.position().toPoint())
        self.cursor_mm.emit(p.x(), p.y())
        super().mouseMoveEvent(e)

    def fit(self):
        r = self.scene().itemsBoundingRect()
        if not r.isEmpty():
            self.fitInView(r.adjusted(-4, -4, 4, 4), Qt.KeepAspectRatio)

    def zoom(self, f):
        self.scale(f, f)
