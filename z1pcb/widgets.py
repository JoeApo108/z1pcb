"""Input widgets and small layout helpers of the sidebar."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QDoubleSpinBox, QFrame, QGridLayout, QLabel, QSpinBox


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
