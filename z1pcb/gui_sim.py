"""Simulation tab: plays a parsed G-code program on the Z1 timeline, removing material from a height map."""

from PySide6.QtCore import QElapsedTimer, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QImage, QPen, QPixmap, QTransform
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QGraphicsEllipseItem,
    QGraphicsPixmapItem,
    QHBoxLayout,
    QLabel,
    QSlider,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import gparse, sim
from .canvas import View
from .fmt import hms, num
from .widgets import Combo

SPEEDS = [('1×', 1), ('10×', 10), ('50×', 50), ('200×', 200), ('1000×', 1000), ('Max', 0)]
MAX_MOVES_PER_TICK = 2500
COPPER = QColor(205, 140, 70)


class SimPanel(QWidget):
    message = Signal(str)  # for the main window status bar

    def __init__(self, colors):
        super().__init__()
        self.prog = self.mat = self.img = self.pending = None
        self.t = 0.0
        self.playing = False
        self.syncing = False  # the slider is being moved by the program, not the user

        self.view = View()
        sc = self.view.scene()
        self.stock = sc.addRect(QRectF(), QPen(QColor('#6b737c'), 0), QBrush(COPPER.darker(118)))
        self.pix = QGraphicsPixmapItem()
        self.pix.setTransformationMode(Qt.SmoothTransformation)
        sc.addItem(self.pix)
        self.tool = QGraphicsEllipseItem()
        self.tool.setZValue(10)
        self.tool.setPen(QPen(QColor('#ffffff'), 0))
        sc.addItem(self.tool)
        pen = QPen(QColor(255, 255, 255, 200), 0)
        self.cross = [sc.addLine(0, 0, 0, 0, pen) for _ in range(2)]
        for c in self.cross:
            c.setZValue(11)

        st = self.style()
        self.i_play, self.i_pause = st.standardIcon(QStyle.SP_MediaPlay), st.standardIcon(QStyle.SP_MediaPause)
        self.b_start = self.button(st.standardIcon(QStyle.SP_MediaSkipBackward), 'Na začiatok', self.to_start)
        self.b_play = self.button(self.i_play, 'Prehrať / pozastaviť (medzerník)', self.toggle)
        self.b_play.setObjectName('Seg')
        self.b_play.setCheckable(True)
        self.b_next = self.button(
            st.standardIcon(QStyle.SP_MediaSkipForward), 'Na ďalšiu výmenu nástroja', self.next_change
        )
        self.speed = Combo()
        self.speed.setFocusPolicy(Qt.StrongFocus)
        for text, value in SPEEDS:
            self.speed.addItem(text, value)
        self.speed.setCurrentIndex(3)
        self.speed.setToolTip('Rýchlosť prehrávania')
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 10000)
        self.slider.setPageStep(250)
        self.slider.valueChanged.connect(self.slider_changed)  # drag, click on the track, keyboard
        self.seek_timer = QTimer(self)  # coalesce a drag into one seek per 40 ms
        self.seek_timer.setSingleShot(True)
        self.seek_timer.setInterval(40)
        self.seek_timer.timeout.connect(self.seek_slider)
        self.time = QLabel('0:00 / 0:00')
        self.time.setMinimumWidth(110)
        self.stop_tc = QCheckBox('Zastaviť pri výmene nástroja')
        self.stop_tc.setChecked(True)
        bar = QHBoxLayout()
        bar.setContentsMargins(12, 8, 12, 8)
        bar.setSpacing(6)
        for w in (self.b_start, self.b_play, self.b_next, self.speed):
            bar.addWidget(w)
        bar.addWidget(self.slider, 1)
        bar.addWidget(self.time)
        bar.addWidget(self.stop_tc)
        self.info = QLabel('')
        self.info.setObjectName('Hint')
        info = QHBoxLayout()
        info.setContentsMargins(12, 0, 12, 8)
        info.addWidget(self.info, 1)
        foot = QWidget()
        fv = QVBoxLayout(foot)
        fv.setContentsMargins(0, 0, 0, 0)
        fv.setSpacing(0)
        fv.addLayout(bar)
        fv.addLayout(info)
        foot.setStyleSheet('background: %s; border-top: 1px solid %s;' % (colors['panel'], colors['border']))
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(self.view, 1)
        v.addWidget(foot)

        self.timer = QTimer(self)
        self.timer.setInterval(33)
        self.timer.timeout.connect(self.tick)
        self.clock = QElapsedTimer()
        self.set_program(None)

    def button(self, icon, tip, fn):
        b = QToolButton()
        b.setIcon(icon)
        b.setToolTip(tip)
        b.clicked.connect(fn)
        return b

    def enable(self, on):
        for w in (self.b_start, self.b_play, self.b_next, self.speed, self.slider):
            w.setEnabled(on)

    # ---------- loading
    def set_program(self, prog):
        """New program to play (or None). The height map is built when the tab is shown."""
        self.pause()
        self.pending, self.prog, self.mat, self.img = prog, None, None, None
        self.t = 0.0
        self.enable(False)
        self.pix.setPixmap(QPixmap())
        self.stock.setRect(QRectF())
        self.tool.setRect(QRectF())
        for c in self.cross:
            c.setLine(0, 0, 0, 0)
        self.set_slider(0)
        self.time.setText('0:00 / 0:00')
        self.info.setText('Najprv vypočítaj alebo otvor G-kód.' if prog is None else '')

    def ensure_loaded(self):
        if self.pending is None or self.prog is not None:
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            prog = self.pending
            unknown = sim.fill_unknown_tools(prog)
            self.mat = m = sim.Material(prog)
            self.img = m.rgb()
            self.prog = prog
        finally:
            QApplication.restoreOverrideCursor()
        self.qimg = QImage(self.img.data, m.nx, m.ny, 3 * m.nx, QImage.Format_RGB888)
        self.pix.setPixmap(QPixmap.fromImage(self.qimg))
        self.pix.setTransform(QTransform(m.res, 0, 0, -m.res, m.ox, m.oy + m.ny * m.res))
        if prog.stock:
            self.stock.setRect(QRectF(0, 0, prog.stock[0], prog.stock[1]))
        self.enable(True)
        self.update_view(force=True)
        self.view.fit()
        notes = ['T%s bez rozmeru v súbore' % ', T'.join(map(str, unknown))] if unknown else []
        notes += prog.warnings[:1]
        self.info.setText(
            'Pohľad na stroj · pohybov %d · výmeny nástroja %d · odhad %s%s'
            % (len(prog.p0), len(prog.events), hms(prog.total), ''.join(' · ' + n for n in notes))
        )
        self.message.emit('Simulácia pripravená')

    # ---------- playback
    def toggle(self):
        if not self.prog:
            return
        if self.playing:
            self.pause()
            return
        if self.t >= self.prog.total:
            self.seek(0.0)
        self.playing = True
        self.b_play.setChecked(True)
        self.b_play.setIcon(self.i_pause)
        self.clock.start()
        self.timer.start()

    def pause(self):
        self.playing = False
        self.timer.stop()
        self.b_play.setChecked(False)
        self.b_play.setIcon(self.i_play)

    def to_start(self):
        self.pause()
        if self.prog:
            self.seek(0.0)

    def next_change(self):
        if not self.prog:
            return
        nxt = [e for e in self.prog.events if e[0] > self.t + 1e-6]
        self.pause()
        self.seek(nxt[0][0] if nxt else self.prog.total)
        if nxt:
            self.show_change(nxt[0])

    def set_slider(self, value):
        self.syncing = True
        self.slider.setValue(value)
        self.syncing = False

    def slider_changed(self, _value):
        if not self.syncing and self.prog:
            self.pause()
            self.seek_timer.start()

    def seek_slider(self):
        if self.prog:
            self.seek(self.prog.total * self.slider.value() / 10000)

    def seek(self, t):
        self.t = max(0.0, min(t, self.prog.total))
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.mat.advance_to(self.mat.index_at(self.t))
        finally:
            QApplication.restoreOverrideCursor()
        self.update_view(force=True)

    def show_change(self, ev):
        tl = self.prog.tools.get(ev[1])
        name = tl.name if tl and tl.name else 'T%d' % ev[1]
        self.info.setText(
            '%s  ·  T%d %s – vlož nástroj, stroj potom sám zmeria jeho dĺžku (≈ %d s).'
            % (hms(ev[0]), ev[1], name, gparse.TOOLCHANGE_S)
        )

    def tick(self):
        if not self.playing or not self.prog:
            return
        dt = self.clock.restart() / 1000.0
        speed = self.speed.currentData()
        old = self.t
        if speed:
            new = self.t + dt * speed
        else:  # Max: a fixed amount of work per frame
            k = min(len(self.prog.p0), self.mat.done + MAX_MOVES_PER_TICK)
            new = float(self.prog.t1[k - 1]) if k else self.prog.total
        if self.stop_tc.isChecked():
            for ev in self.prog.events:
                if old < ev[0] <= new and ev[0] > 0.5:
                    new = ev[0]
                    self.pause()
                    self.show_change(ev)
                    break
        self.t = min(new, self.prog.total)
        self.mat.advance_to(self.mat.index_at(self.t))
        self.update_view()
        if self.t >= self.prog.total:
            self.pause()
            self.info.setText('Koniec programu · %s' % hms(self.prog.total))

    def update_view(self, force=False):
        prog = self.prog
        pos, k = self.mat.position(self.t)
        if len(prog.p0) and self.t > prog.t0[k]:  # material removed so far by the move in progress
            self.mat.stamp(prog.p0[k], pos, int(prog.tool[k]))
        if self.mat.update_image(self.img) is not None or force:
            self.pix.setPixmap(QPixmap.fromImage(self.qimg))
        tool = int(prog.tool[k]) if len(prog.tool) else 0
        tl = prog.tools.get(tool)
        r = max(tl.radius_at(-pos[2]) if tl else 0.5, 0.15)
        self.tool.setRect(QRectF(pos[0] - r, pos[1] - r, 2 * r, 2 * r))
        arm = 3.0
        self.cross[0].setLine(pos[0] - arm, pos[1], pos[0] + arm, pos[1])
        self.cross[1].setLine(pos[0], pos[1] - arm, pos[0], pos[1] + arm)
        self.tool.setBrush(QBrush(QColor(239, 68, 68, 150) if pos[2] < 0 else QColor(59, 130, 246, 90)))
        self.set_slider(int(10000 * self.t / max(prog.total, 1e-9)))
        self.time.setText('%s / %s' % (hms(self.t), hms(prog.total)))
        if (self.playing or force) and len(prog.p0):
            f = prog.feed[k]
            name = tl.name if tl and tl.name else ''
            move = ('posuv %d mm/min' % f) if f else 'rýchloposuv'
            self.info.setText(
                'T%d %s  ·  Z %s mm  ·  %s  ·  riadok %d' % (tool, name, num(pos[2], 3), move, int(prog.line[k]))
            )
