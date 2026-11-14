"""Input widgets and small layout helpers of the sidebar."""

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QComboBox, QDoubleSpinBox, QFrame, QGridLayout, QLabel, QLayout, QSpinBox


class NoWheel:
    """The mouse wheel changes the value only after the field was clicked; otherwise it scrolls the panel."""

    def wheelEvent(self, e):
        if self.hasFocus():
            super().wheelEvent(e)
        else:
            e.ignore()


class Spin(NoWheel, QDoubleSpinBox):
    def validate(self, text, pos):
        """Accept a decimal dot as well as the comma of the Slovak locale."""
        return super().validate(text.replace('.', self.locale().decimalPoint()), pos)


class ISpin(NoWheel, QSpinBox):
    pass


class Combo(NoWheel, QComboBox):
    pass


def spin(value, lo, hi, dec, suffix, step=1.0, width=110):
    s = Spin()
    s.setFocusPolicy(Qt.StrongFocus)
    s.setRange(lo, hi)
    s.setDecimals(dec)
    s.setValue(value)
    s.setSuffix(suffix)
    s.setSingleStep(step)
    s.setAlignment(Qt.AlignRight)
    s.setButtonSymbols(QDoubleSpinBox.NoButtons)
    s.setFixedWidth(width)
    return s


def ispin(value, lo, hi, suffix='', step=1, width=110):
    s = ISpin()
    s.setFocusPolicy(Qt.StrongFocus)
    s.setRange(lo, hi)
    s.setValue(value)
    s.setSuffix(suffix)
    s.setSingleStep(step)
    s.setAlignment(Qt.AlignRight)
    s.setButtonSymbols(QSpinBox.NoButtons)
    s.setFixedWidth(width)
    return s


def section(text):
    lb = QLabel(text.upper())
    lb.setObjectName('Section')
    return lb


def hint(text):
    lb = QLabel(text)
    lb.setObjectName('Hint')
    lb.setWordWrap(True)
    return lb


def card():
    f = QFrame()
    f.setObjectName('Card')
    g = QGridLayout(f)
    g.setContentsMargins(12, 10, 12, 10)
    g.setHorizontalSpacing(10)
    g.setVerticalSpacing(8)
    return f, g


class FlowLayout(QLayout):
    """Lays widgets out left to right and wraps to the next row when the width runs out (Qt's flow layout)."""

    def __init__(self, parent=None, margins=(12, 8, 12, 8), spacing=6):
        super().__init__(parent)
        self.items = []
        self.setContentsMargins(*margins)
        self.setSpacing(spacing)

    def addItem(self, item):
        self.items.append(item)

    def count(self):
        return len(self.items)

    def itemAt(self, i):
        return self.items[i] if 0 <= i < len(self.items) else None

    def takeAt(self, i):
        return self.items.pop(i) if 0 <= i < len(self.items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self.arrange(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self.arrange(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for it in self.items:
            size = size.expandedTo(it.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    def arrange(self, rect, test):
        m = self.contentsMargins()
        area = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, row = area.x(), area.y(), 0
        for it in self.items:
            if it.isEmpty():  # hidden widget
                continue
            size = it.sizeHint()
            if x + size.width() > area.right() + 1 and row > 0:
                x, y, row = area.x(), y + row + self.spacing(), 0
            if not test:
                it.setGeometry(QRect(QPoint(x, y), size))
            x += size.width() + self.spacing()
            row = max(row, size.height())
        return y + row - rect.y() + m.bottom()
