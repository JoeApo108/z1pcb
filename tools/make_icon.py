"""Render the app icon (PCB trace motif) to assets/z1pcb.png and a multi-size PNG-in-ICO assets/z1pcb.ico."""

import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPainterPath, QPen


def render(n):
    im = QImage(n, n, QImage.Format_ARGB32)
    im.fill(Qt.transparent)
    p = QPainter(im)
    p.setRenderHint(QPainter.Antialiasing)
    s = n / 256
    p.setBrush(QColor('#1d2025'))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(QRectF(8 * s, 8 * s, 240 * s, 240 * s), 48 * s, 48 * s)
    cu = QColor('#d39a43')
    p.setPen(QPen(cu, 22 * s, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.setBrush(Qt.NoBrush)
    path = QPainterPath(QPointF(66 * s, 76 * s))
    path.lineTo(190 * s, 76 * s)
    path.lineTo(66 * s, 180 * s)
    path.lineTo(190 * s, 180 * s)
    p.drawPath(path)
    p.setPen(Qt.NoPen)
    p.setBrush(cu)
    for x, y in ((66, 76), (190, 180)):
        p.drawEllipse(QPointF(x * s, y * s), 26 * s, 26 * s)
    p.setBrush(QColor('#1d2025'))
    for x, y in ((66, 76), (190, 180)):
        p.drawEllipse(QPointF(x * s, y * s), 10 * s, 10 * s)
    p.setPen(QPen(QColor('#3b82f6'), 7 * s))
    p.setBrush(Qt.NoBrush)
    p.drawRoundedRect(QRectF(8 * s, 8 * s, 240 * s, 240 * s), 48 * s, 48 * s)
    p.end()
    return im


def png(im):
    ba = QByteArray()
    b = QBuffer(ba)
    b.open(QIODevice.WriteOnly)
    im.save(b, 'PNG')
    return bytes(ba)


app = QGuiApplication(sys.argv)
out = Path(__file__).resolve().parents[1] / 'assets'
out.mkdir(exist_ok=True)
render(256).save(str(out / 'z1pcb.png'))
sizes = [16, 24, 32, 48, 64, 128, 256]
blobs = [png(render(n)) for n in sizes]
head = struct.pack('<HHH', 0, 1, len(sizes))
off = 6 + 16 * len(sizes)
dirs = b''
for n, b in zip(sizes, blobs):
    dirs += struct.pack('<BBBBHHII', n % 256, n % 256, 0, 0, 1, 32, len(b), off)
    off += len(b)
(out / 'z1pcb.ico').write_bytes(head + dirs + b''.join(blobs))
print('icon written', out / 'z1pcb.ico')
