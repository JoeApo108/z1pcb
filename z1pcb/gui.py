"""Z1 PCB: desktop app (PySide6). Gerber + Excellon -> checked G-code for Makera Z1."""

import os
import subprocess
import sys
import tempfile
import traceback
import urllib.error
from pathlib import Path

from PySide6.QtCore import (
    QLibraryInfo,
    QLocale,
    QObject,
    QPointF,
    QRectF,
    QSettings,
    Qt,
    QThread,
    QTimer,
    QTranslator,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtGui import QAction, QBrush, QColor, QDesktopServices, QIcon, QKeySequence, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QFileDialog,
    QGraphicsItem,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import __version__ as VERSION
from . import applog, cam, gcode, gerberio, job, makera_lib, updates
from .applog import log
from .canvas import View
from .errors import UserError
from .fmt import hms, num
from .geom import as_polys
from .gui_sim import SimPanel
from .theme import BANNER, LAYER, C, apply_theme, swatch
from .widgets import Combo, card, hint, ispin, section, spin

APP = 'Z1 PCB'
STAGES = {
    'clear': 'Čistenie voľnej medi (plochá fréza)',
    'isolate': 'Izolácia (rytec)',
    'rest': 'Dočistenie úzkych miest (rytec)',
    'holes': 'Otvory a drážky',
    'outline': 'Výrezy a obrys',
    'write': 'Zápis G-kódu',
    'parse': 'Čítanie G-kódu',
    'check': 'Kontrola výsledku',
}
STATE_TEXT = {'ok': 'v poriadku', 'bad': 'chyba', 'warn': 'pozor', 'info': 'info'}
STATE_COLOR = {'ok': C['ok'], 'bad': C['bad'], 'warn': C['warn'], 'info': C['muted']}
ANCHORS = (('center', 'V strede polotovaru'), ('corner', 'V ľavom prednom rohu'))
ROLES = (
    ('clear', 'T4', 'Plochá fréza'),
    ('vbit', 'T2', 'Rytec – hrot'),
    ('corn', 'T3', 'Kukuričná fréza'),
    ('mask', 'T5', 'Odstránenie masky – hrot'),
)
ROLE_FIELDS = {  # fields a library tool fills in; editing one of them by hand means 'own values' again
    'clear': ('t_clear', 'feed_clear', 'plunge_clear', 'rpm_clear', 'clear_pct'),
    'vbit': ('t_vtip', 't_vang', 'feed_vbit', 'plunge_vbit', 'rpm_vbit'),
    'corn': ('t_corn', 'feed_corn', 'plunge_corn', 'rpm_corn', 'level_step'),
    'mask': ('t_mask', 'feed_mask', 'plunge_mask', 'rpm_mask', 'mask_depth'),
}
MASK_PAUSE = (
    'Koniec 1. programu. Dosku nechaj prilepenú a nulový bod nemeň: očisti ju, nanes UV masku a vytvrď ju. '
    'Potom spusti 2. program.'
)


# ---------- background work
class Cancelled(Exception):
    pass


class Job(QObject):
    """Runs fn(say) in a worker thread. Results come back tagged, so a stale one can be recognised."""

    progress = Signal(str, int)
    done = Signal(object)  # (tag, result)
    failed = Signal(object)  # (tag, kind, message): kind is 'cancelled', 'user' or 'bug'

    def __init__(self, fn, tag):
        super().__init__()
        self.fn, self.tag, self.cancelled = fn, tag, False

    def say(self, stage, pct):
        if self.cancelled:
            raise Cancelled()
        self.progress.emit(stage, pct)

    def run(self):
        try:
            result = self.fn(self)
        except Cancelled:
            self.failed.emit((self.tag, 'cancelled', ''))
        except UserError as e:
            self.failed.emit((self.tag, 'user', str(e)))
        except Exception as e:
            log.error('job failed\n%s', traceback.format_exc())
            self.failed.emit((self.tag, 'bug', '%s: %s' % (type(e).__name__, e)))
        else:
            self.done.emit((self.tag, result))


class Bridge(QObject):
    """Carries uncaught errors from any thread to the window."""

    error = Signal(str)


# ---------- drawing helpers
def poly_path(g):
    path = QPainterPath()
    for p in as_polys(g):
        for ring in [p.exterior] + list(p.interiors):
            c = list(ring.coords)
            if len(c) < 3:
                continue
            path.moveTo(QPointF(*c[0]))
            for x, y in c[1:]:
                path.lineTo(QPointF(x, y))
            path.closeSubpath()
    path.setFillRule(Qt.OddEvenFill)
    return path


def lines_path(lines):
    path = QPainterPath()
    for pts in lines:
        if len(pts) < 2:
            continue
        path.moveTo(QPointF(*pts[0]))
        for x, y in pts[1:]:
            path.lineTo(QPointF(x, y))
    return path


def field_value(w):
    if isinstance(w, QCheckBox):
        return w.isChecked()
    return w.currentData() if isinstance(w, Combo) else w.value()


def make_store():
    """User settings; tests and self-tests point Z1PCB_SETTINGS at a throw-away file."""
    path = os.environ.get('Z1PCB_SETTINGS')
    return QSettings(path, QSettings.IniFormat) if path else QSettings('z1pcb', APP)


# ---------- main window
class Main(QMainWindow):
    finished = Signal(bool)  # emitted in the GUI thread after a job (used by the self-test)

    def __init__(self):
        super().__init__()
        self.resize(1440, 900)
        self.store = make_store()
        self.design = None  # loaded files (gerberio.Design)
        self.result = None  # job.Result that matches the design and the settings on screen
        self.paths_ = {'copper': '', 'outline': '', 'drills': [], 'mask': ''}
        self.detected = None
        self.epoch = 0  # grows with every change of files or settings; a result is valid for one epoch
        self.job = None  # running compute / check job
        self.threads = []
        self.release = None
        self.auto_update = False  # --update-test: install without asking
        self.update_checked = False
        self.fields = {}

        left = self.build_sidebar()
        center = self.build_preview()
        results = self.build_results()
        right = QSplitter(Qt.Vertical)
        right.addWidget(center)
        right.addWidget(results)
        right.setSizes([620, 260])
        right.setHandleWidth(1)
        split = QSplitter()
        split.addWidget(left)
        split.addWidget(right)
        split.setSizes([390, 1050])
        split.setHandleWidth(1)
        self.setCentralWidget(split)
        self.build_status_and_menu()
        self.restore_settings()
        self.set_idle_banner()
        self.setWindowTitle('%s %s' % (APP, VERSION))
        self.setAcceptDrops(True)

    # ---------- construction
    def add_field(self, key, widget):
        self.fields[key] = widget
        return widget

    def build_sidebar(self):
        side = QWidget()
        side.setObjectName('Sidebar')
        sv = QVBoxLayout(side)
        sv.setContentsMargins(16, 6, 16, 16)
        sv.setSpacing(6)
        tools, prm = cam.default_tools(), cam.Params()

        sv.addWidget(section('Vstupné súbory'))
        box, g = card()
        open_btn = QPushButton('Otvoriť priečinok s výstupom z návrhu…')
        open_btn.clicked.connect(self.pick_folder)
        g.addWidget(open_btn, 0, 0, 1, 3)
        self.file_lbl = {}
        files = (('copper', 'Meď'), ('outline', 'Obrys'), ('drills', 'Vŕtanie'), ('mask', 'Maska'))
        for r, (key, label) in enumerate(files, 1):
            g.addWidget(QLabel(label), r, 0)
            v = QLabel('—')
            v.setObjectName('FileMissing')
            v.setTextInteractionFlags(Qt.TextSelectableByMouse)
            v.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            self.file_lbl[key] = v
            g.addWidget(v, r, 1)
            b = QToolButton()
            b.setText('Zmeniť')
            b.clicked.connect(lambda _=False, k=key: self.pick_file(k))
            g.addWidget(b, r, 2)
        g.addWidget(QLabel('Strana'), 5, 0)
        seg = QHBoxLayout()
        seg.setSpacing(0)
        self.side_grp = QButtonGroup(self)
        for i, (key, text) in enumerate((('bottom', 'Spodná'), ('top', 'Vrchná'))):
            b = QToolButton()
            b.setObjectName('Seg')
            b.setText(text)
            b.setCheckable(True)
            b.setProperty('side', key)
            self.side_grp.addButton(b, i)
            seg.addWidget(b)
        self.side_grp.button(0).setChecked(True)
        self.side_grp.idToggled.connect(self.side_changed)
        seg.addStretch(1)
        g.addLayout(seg, 5, 1, 1, 2)
        g.setColumnStretch(1, 1)
        sv.addWidget(box)
        sv.addWidget(
            hint(
                'Stačí vybrať priečinok alebo ho pretiahnuť do okna, vrstvy sa rozpoznajú samé. '
                'Vŕtanie môže chýbať. Frézuje sa jedna strana; obojstranné dosky aplikácia po otočení nezarovná.'
            )
        )

        sv.addWidget(section('Polotovar'))
        box, g = card()
        auto = self.add_field('stock_auto', QCheckBox('Najmenší možný podľa dosky'))
        auto.setChecked(True)
        g.addWidget(auto, 0, 0, 1, 2)
        rows = (
            ('Šírka (X)', self.add_field('stock_w', spin(100, 10, 200, 1, ' mm'))),
            ('Výška (Y)', self.add_field('stock_h', spin(100, 10, 200, 1, ' mm'))),
            ('Hrúbka dosky', self.add_field('thick', spin(prm.thickness, 0.4, 3.2, 2, ' mm', 0.1))),
        )
        for r, (label, w) in enumerate(rows, 1):
            g.addWidget(QLabel(label), r, 0)
            g.addWidget(w, r, 1, Qt.AlignRight)
        anchor = self.add_field('anchor', Combo())
        anchor.setFocusPolicy(Qt.StrongFocus)
        for key, text in ANCHORS:
            anchor.addItem(text, key)
        anchor.setMinimumContentsLength(12)
        anchor.setSizeAdjustPolicy(Combo.AdjustToMinimumContentsLengthWithIcon)
        g.addWidget(QLabel('Umiestnenie dosky'), 4, 0)
        g.addWidget(anchor, 4, 1)
        sv.addWidget(box)

        sv.addWidget(section('Nástroje'))
        box, g = card()
        self.library = makera_lib.load()
        size = {
            'clear': self.add_field('t_clear', spin(tools['clear'].diameter, 0.5, 3.175, 2, ' mm', 0.1)),
            'vbit': self.add_field('t_vtip', spin(tools['vbit'].diameter, 0.05, 0.5, 2, ' mm', 0.05)),
            'corn': self.add_field('t_corn', spin(tools['corn'].diameter, 0.3, 3.175, 2, ' mm', 0.1)),
            'mask': self.add_field('t_mask', spin(tools['mask'].diameter, 0.1, 1.0, 2, ' mm', 0.05)),
        }
        row = 0
        for role, number, label in ROLES:
            n = QLabel(number)
            n.setObjectName('Hint')
            pick = self.add_field('lib_' + role, Combo())
            pick.setFocusPolicy(Qt.StrongFocus)
            pick.setSizeAdjustPolicy(Combo.AdjustToMinimumContentsLengthWithIcon)
            pick.setMinimumContentsLength(10)
            pick.addItem('Vlastné hodnoty', '')
            for t in self.library.for_role(role) if self.library else []:
                pick.addItem(t.label(), t.key)
                tip = '%s\n%s · %d ot./min · posuv %d · zanorenie %d mm/min' % (
                    t.name,
                    t.material,
                    t.rpm,
                    t.feed,
                    t.plunge,
                )
                pick.setItemData(pick.count() - 1, tip, Qt.ToolTipRole)
            pick.setEnabled(self.library is not None)
            g.addWidget(n, row, 0)
            g.addWidget(QLabel(label), row, 1)
            g.addWidget(size[role], row, 2, Qt.AlignRight)
            row += 1
            if role == 'vbit':
                g.addWidget(QLabel('Rytec – uhol'), row, 1)
                angle = self.add_field('t_vang', spin(2 * tools['vbit'].half_angle, 10, 90, 0, ' °', 5))
                g.addWidget(angle, row, 2, Qt.AlignRight)
                row += 1
            g.addWidget(pick, row, 1, 1, 2)  # full width: Makera's names are long
            row += 1
        if self.library:
            text = (
                'Knižnica Makera Studio (%s): výber nástroja nastaví jeho priemer a rezné podmienky.'
                % self.library.updated
            )
        else:
            text = 'Makera Studio sa na tomto počítači nenašlo: rozmery a rezné podmienky zadaj ručne.'
        g.addWidget(hint(text), row, 0, 1, 3)
        g.setColumnStretch(1, 1)
        sv.addWidget(box)

        sv.addWidget(section('Frézovanie'))
        box, g = card()
        copper = self.add_field('copper_um', Combo())
        copper.setFocusPolicy(Qt.StrongFocus)
        for um in sorted(cam.COPPER_DEPTH):
            copper.addItem('%d µm' % um, str(um))
        copper.setCurrentIndex(copper.findData('35'))
        copper.setFixedWidth(110)
        g.addWidget(QLabel('Hrúbka medi'), 0, 0)
        g.addWidget(copper, 0, 1, Qt.AlignRight)
        g.addWidget(QLabel('Hĺbka do medi'), 1, 0)
        g.addWidget(self.add_field('depth', spin(prm.copper_depth, 0.04, 0.2, 3, ' mm', 0.01)), 1, 1, Qt.AlignRight)
        g.addWidget(hint('Hĺbka sa nastaví podľa hrúbky medi (meď + ~0,03 mm), dá sa zmeniť.'), 2, 0, 1, 2)
        g.addWidget(QLabel('Mostíky obrysu'), 3, 0)
        g.addWidget(self.add_field('tabs', ispin(prm.tab_count, 0, 12)), 3, 1, Qt.AlignRight)
        full = self.add_field('full', QCheckBox('Odstrániť všetku voľnú meď'))
        full.setChecked(True)
        g.addWidget(full, 4, 0, 1, 2)
        text = (
            'Nutné pri doskách so sieťovým napätím: ostrovčeky medi medzi spojmi skracujú izolačnú vzdialenosť. '
            'Vypnuté: izolačná drážka okolo spojov a odstránenie úzkych pásikov medi, bez plochej frézy.'
        )
        g.addWidget(hint(text), 5, 0, 1, 2)
        self.add_field('with_mask', QCheckBox('S nepájivou maskou (2 programy)')).setEnabled(False)
        g.addWidget(self.fields['with_mask'], 6, 0, 1, 2)
        text = (
            '1. program frézuje meď. Potom na dosku nanesieš UV masku, 2. program odkryje plôšky a až potom '
            'vŕta a reže obrys. Potrebuje súbor masky (napr. B.Mask) a nástroj na masku (T5).'
        )
        g.addWidget(hint(text), 7, 0, 1, 2)
        sv.addWidget(box)

        self.adv_btn = QToolButton()
        self.adv_btn.setObjectName('Link')
        self.adv_btn.setCheckable(True)
        self.adv_btn.toggled.connect(self.show_advanced)
        sv.addWidget(self.adv_btn)
        self.adv_box, g = card()
        for c, text in enumerate(('Posuv', 'Zanorenie', 'Otáčky'), 1):
            h = QLabel(text)
            h.setObjectName('Hint')
            g.addWidget(h, 0, c, Qt.AlignRight)
        for r, (key, number) in enumerate((('clear', 'T4'), ('vbit', 'T2'), ('corn', 'T3'), ('mask', 'T5')), 1):
            tl = tools[key]
            g.addWidget(QLabel(number), r, 0)
            g.addWidget(self.add_field('feed_' + key, ispin(tl.feed, 20, 1200, step=50, width=78)), r, 1)
            g.addWidget(self.add_field('plunge_' + key, ispin(tl.plunge, 20, 600, step=20, width=78)), r, 2)
            g.addWidget(self.add_field('rpm_' + key, ispin(tl.rpm, 3000, 13000, step=500, width=78)), r, 3)
        g.addWidget(hint('Posuv a zanorenie v mm/min, otáčky za minútu.'), 5, 0, 1, 4)
        rows = (
            ('Hĺbka na prechod', 'level_step', spin(prm.level_step, 0.1, 1.0, 2, ' mm', 0.05, width=78)),
            ('Krok plochej frézy', 'clear_pct', spin(prm.clear_step * 50, 20, 90, 0, ' %', 5, width=78)),
            ('Výška priblíženia', 'approach_z', spin(prm.approach_z, 0.2, 2.0, 2, ' mm', 0.1, width=78)),
            ('Hĺbka do masky (T5)', 'mask_depth', spin(prm.mask_depth, 0.02, 0.5, 2, ' mm', 0.05, width=78)),
        )
        for r, (label, key, w) in enumerate(rows, 6):
            g.addWidget(QLabel(label), r, 0, 1, 3)
            g.addWidget(self.add_field(key, w), r, 3, Qt.AlignRight)
        text = (
            'Otvory, výrezy a obrys sa režú po vrstvách. Krok plochej frézy je v percentách jej priemeru. '
            'Nad výškou priblíženia ide nástroj rýchloposuvom. Nástroj na masku pruží, hĺbka je jeho stlačenie.'
        )
        g.addWidget(hint(text), 10, 0, 1, 4)
        sv.addWidget(self.adv_box)
        sv.addStretch(1)

        self.run_btn = QPushButton('Vypočítať a skontrolovať')
        self.run_btn.setObjectName('Primary')
        self.run_btn.clicked.connect(self.compute)
        self.run_btn.setShortcut(QKeySequence('Ctrl+R'))
        self.save_btn = QPushButton('Uložiť G-kód…')
        self.save_btn.clicked.connect(self.save)
        guide = QToolButton()
        guide.setObjectName('Link')
        guide.setText('Postup na stroji')
        guide.clicked.connect(self.machine_guide)
        scroll = QScrollArea()
        scroll.setWidget(side)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        actions = QWidget()
        actions.setObjectName('ActionBar')
        av = QVBoxLayout(actions)
        av.setContentsMargins(16, 12, 16, 12)
        av.setSpacing(8)
        av.addWidget(self.run_btn)
        av.addWidget(self.save_btn)
        av.addWidget(guide, 0, Qt.AlignHCenter)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(0)
        lv.addWidget(scroll, 1)
        lv.addWidget(actions)
        left.setMinimumWidth(360)
        left.setMaximumWidth(460)
        return left

    def build_preview(self):
        self.view = View()
        self.view.cursor_mm.connect(lambda x, y: self.coord.setText('návrh X %s   Y %s mm' % (num(x, 2), num(y, 2))))
        bar = QHBoxLayout()
        bar.setContentsMargins(12, 8, 12, 8)
        bar.setSpacing(4)
        self.mode_grp = QButtonGroup(self)
        for i, text in enumerate(('Dráhy', 'Simulácia')):
            b = QToolButton()
            b.setObjectName('Seg')
            b.setText(text)
            b.setCheckable(True)
            self.mode_grp.addButton(b, i)
            bar.addWidget(b)
        self.mode_grp.button(0).setChecked(True)
        self.mode_grp.idClicked.connect(self.set_mode)
        bar.addSpacing(16)
        self.layer_box = QWidget()
        lb = QHBoxLayout(self.layer_box)
        lb.setContentsMargins(0, 0, 0, 0)
        lb.setSpacing(4)
        self.show_layer = {}
        layers = (
            ('board', 'Doska a polotovar'),
            ('copper', 'Meď'),
            (4, 'Plochá fréza'),
            (2, 'Rytec'),
            (3, 'Otvory, obrys, mostíky'),
            (5, 'Maska'),
            ('problem', 'Chyby'),
        )
        for key, label in layers:
            b = QToolButton()
            b.setObjectName('Layer')
            b.setText(label)
            b.setIcon(swatch(LAYER[key]))
            b.setCheckable(True)
            b.setChecked(True)
            b.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            b.toggled.connect(lambda on, b=b, key=key: self.layer_toggled(b, key, on))
            self.show_layer[key] = b
            lb.addWidget(b)
        note = QLabel('pohľad ako v návrhu')
        note.setObjectName('Hint')
        lb.addSpacing(8)
        lb.addWidget(note)
        bar.addWidget(self.layer_box)
        bar.addStretch(1)
        zoom = (
            ('−', lambda: self.cur_view().zoom(1 / 1.25)),
            ('+', lambda: self.cur_view().zoom(1.25)),
            ('Prispôsobiť', lambda: self.cur_view().fit()),
        )
        for text, fn in zoom:
            b = QToolButton()
            b.setText(text)
            b.clicked.connect(fn)
            bar.addWidget(b)
        self.sim = SimPanel(C)
        self.sim.message.connect(lambda m: self.stage.setText(m))
        self.sim.view.cursor_mm.connect(
            lambda x, y: self.coord.setText('stroj X %s   Y %s mm' % (num(x, 2), num(y, 2)))
        )
        self.stack = QStackedWidget()
        self.stack.addWidget(self.view)
        self.stack.addWidget(self.sim)
        head = QWidget()
        head.setLayout(bar)
        head.setStyleSheet('background: %s; border-bottom: 1px solid %s;' % (C['panel'], C['border']))
        center = QWidget()
        cv = QVBoxLayout(center)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(0)
        cv.addWidget(head)
        cv.addWidget(self.stack, 1)
        return center

    def build_results(self):
        res = QWidget()
        res.setObjectName('ResultPane')
        rv = QVBoxLayout(res)
        rv.setContentsMargins(12, 10, 12, 12)
        rv.setSpacing(8)
        self.banner = QLabel('')
        self.banner.setObjectName('Banner')
        self.banner.setWordWrap(True)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(['Kontrola', 'Hodnota', 'Stav'])
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setFocusPolicy(Qt.NoFocus)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.details = QLabel('')
        self.details.setObjectName('Hint')
        self.details.setWordWrap(True)
        self.details.setTextInteractionFlags(Qt.TextSelectableByMouse)
        rv.addWidget(self.banner)
        rv.addWidget(self.table, 1)
        rv.addWidget(self.details)
        return res

    def build_status_and_menu(self):
        sb = QStatusBar()
        self.setStatusBar(sb)
        self.stage = QLabel('Pripravené')
        self.bar = QProgressBar()
        self.bar.setFixedWidth(220)
        self.bar.setVisible(False)
        self.coord = QLabel('')
        sb.addWidget(self.stage, 1)
        sb.addPermanentWidget(self.bar)
        sb.addPermanentWidget(self.coord)
        self.upd_btn = QPushButton()
        self.upd_btn.setObjectName('Primary')
        self.upd_btn.setVisible(False)
        self.upd_btn.clicked.connect(lambda: self.offer_update(self.release))
        sb.addPermanentWidget(self.upd_btn)

        def action(menu, text, fn, key=None):
            a = QAction(text, self)
            a.triggered.connect(fn)
            if key:
                a.setShortcut(key)
            menu.addAction(a)
            return a

        m = self.menuBar().addMenu('Súbor')
        action(m, 'Otvoriť priečinok…', self.pick_folder, QKeySequence.Open)
        action(m, 'Znova načítať súbory', self.reload, QKeySequence('F5'))
        self.act_save = action(m, 'Uložiť G-kód…', self.save, QKeySequence.Save)
        self.act_check = action(m, 'Skontrolovať iný G-kód (napr. zo Studia)…', self.check_external)
        h = self.menuBar().addMenu('Pomoc')
        action(h, 'Postup na stroji…', self.machine_guide, QKeySequence('F1'))
        action(h, 'Otvoriť priečinok s logom', self.open_log_dir)
        action(h, 'Skontrolovať aktualizácie…', lambda: self.check_updates(manual=True))
        action(h, 'Licencie…', self.open_licenses)
        action(h, 'O programe', self.about)
        self.update_actions()

    # ---------- settings
    def restore_settings(self):
        for key, w in self.fields.items():
            if not self.store.contains(key):
                continue
            w.blockSignals(True)
            if isinstance(w, QCheckBox):
                w.setChecked(self.store.value(key, type=bool))
            elif isinstance(w, Combo):
                w.setCurrentIndex(max(0, w.findData(self.store.value(key, type=str))))
            else:
                w.setValue(self.store.value(key, type=type(w.value())))
            w.blockSignals(False)
        for w in self.fields.values():
            if isinstance(w, QCheckBox):
                w.toggled.connect(self.param_changed)
            elif isinstance(w, Combo):
                w.currentIndexChanged.connect(self.param_changed)
            else:
                w.valueChanged.connect(self.param_changed)
        self.fields['copper_um'].currentIndexChanged.connect(self.copper_changed)
        self.applying_tool = False
        for role, fields in ROLE_FIELDS.items():
            self.fields['lib_' + role].currentIndexChanged.connect(lambda _i, role=role: self.library_tool_chosen(role))
            for key in fields:
                self.fields[key].valueChanged.connect(lambda *_, role=role: self.tool_value_edited(role))
        self.match_library_tools()
        self.adv_btn.setChecked(self.store.value('advanced_open', False, type=bool))
        self.show_advanced(self.adv_btn.isChecked())
        self.sync_stock_fields()

    def save_settings(self):
        for key, w in self.fields.items():
            self.store.setValue(key, field_value(w))

    def show_advanced(self, on):
        self.adv_box.setVisible(on)
        self.adv_btn.setText(('▾' if on else '▸') + ' Rezné podmienky')
        self.store.setValue('advanced_open', on)

    def settings(self):
        f = {key: field_value(w) for key, w in self.fields.items()}
        tools = cam.default_tools()
        tools['clear'].diameter, tools['corn'].diameter = f['t_clear'], f['t_corn']
        tools['vbit'].diameter, tools['vbit'].half_angle = f['t_vtip'], f['t_vang'] / 2
        tools['clear'].name = 'Flat End %.3g mm' % f['t_clear']
        tools['vbit'].name = 'Engraving %g deg x %.3g mm' % (f['t_vang'], f['t_vtip'])
        tools['corn'].name = 'Corn %.3g mm' % f['t_corn']
        tools['mask'].diameter = f['t_mask']
        tools['mask'].name = 'Solder Mask Removal %.3g mm' % f['t_mask']
        for role in tools:  # a library tool keeps Makera's name, which Studio shows
            t = self.library.get(f['lib_' + role]) if self.library and f['lib_' + role] else None
            if t:
                tools[role].name = t.name
        for key, tl in tools.items():
            tl.feed, tl.plunge, tl.rpm = f['feed_' + key], f['plunge_' + key], f['rpm_' + key]
        prm = cam.Params(
            thickness=f['thick'],
            copper_thickness=int(f['copper_um']) / 1000,
            copper_depth=f['depth'],
            tab_count=f['tabs'],
            full_clear=f['full'],
            mask=f['with_mask'] and self.design is not None and self.design.mask is not None,
            mask_depth=f['mask_depth'],
            level_step=f['level_step'],
            approach_z=f['approach_z'],
            clear_step=f['clear_pct'] / 50,  # % of the diameter -> fraction of the radius
        )
        return tools, prm, (f['stock_w'], f['stock_h']), f['anchor']

    def sync_stock_fields(self):
        """With 'smallest possible' on, the stock follows the board and the tools; the two fields are read-only."""
        auto = self.fields['stock_auto'].isChecked()
        for key in ('stock_w', 'stock_h'):
            self.fields[key].setEnabled(not auto)
        if auto and self.design:
            tools, prm, _, _ = self.settings()
            size = job.fit_stock(self.design.board, tools, prm)
            for key, v in zip(('stock_w', 'stock_h'), size):
                w = self.fields[key]
                w.blockSignals(True)
                w.setValue(min(v, w.maximum()))
                w.blockSignals(False)

    def param_changed(self, *_):
        self.sync_stock_fields()
        self.save_settings()
        self.invalidate('Nastavenia sa zmenili – vypočítaj znova.')

    def library_tool_chosen(self, role):
        """Fill in the size and cutting data of the tool picked from Makera's library."""
        key = self.fields['lib_' + role].currentData()
        t = self.library.get(key) if self.library and key else None
        if not t:
            return
        f = self.fields
        self.applying_tool = True
        try:
            if role == 'vbit':
                f['t_vtip'].setValue(t.diameter)
                f['t_vang'].setValue(2 * t.half_angle)
            else:
                f['t_' + role].setValue(t.diameter)
            f['feed_' + role].setValue(t.feed)
            f['plunge_' + role].setValue(t.plunge)
            f['rpm_' + role].setValue(t.rpm)
            if role == 'corn' and t.step_down:
                f['level_step'].setValue(t.step_down)
            if role == 'clear' and t.step_over_pct:
                f['clear_pct'].setValue(t.step_over_pct)
        finally:
            self.applying_tool = False
        self.save_settings()

    def tool_value_edited(self, role):
        """A value changed by hand no longer belongs to the library tool shown: switch to 'own values'."""
        pick = self.fields['lib_' + role]
        if self.applying_tool or not pick.currentData():
            return
        pick.blockSignals(True)
        pick.setCurrentIndex(0)
        pick.blockSignals(False)
        self.store.setValue('lib_' + role, '')

    def match_library_tools(self):
        """Show the library tool that the current values belong to (first start, or values typed by hand)."""
        if not self.library:
            return
        f = {key: field_value(w) for key, w in self.fields.items()}
        for role, _, _ in ROLES:
            pick = self.fields['lib_' + role]
            if pick.currentData():
                continue
            size = (f['t_vtip'], f['t_vang'] / 2) if role == 'vbit' else (f['t_' + role], 0.0)
            cut = (f['feed_' + role], f['plunge_' + role], f['rpm_' + role])
            for t in self.library.for_role(role):
                if t.custom or (t.diameter, t.half_angle) != size or (t.feed, t.plunge, t.rpm) != cut:
                    continue
                pick.blockSignals(True)
                pick.setCurrentIndex(pick.findData(t.key))
                pick.blockSignals(False)
                break

    def copper_changed(self, _index):
        """A different copper foil: suggest the depth that goes through it (the user may still change it)."""
        self.fields['depth'].setValue(cam.COPPER_DEPTH[int(self.fields['copper_um'].currentData())])

    def side_changed(self, _id, on):
        if not on:
            return
        if self.detected:
            self.apply_detect(None)  # the folder may hold the other side's copper
        else:
            self.invalidate('Strana sa zmenila – vypočítaj znova.')

    def side(self):
        return self.side_grp.checkedButton().property('side')

    # ---------- state
    def busy(self):
        return self.job is not None

    def update_actions(self):
        ready = self.design is not None and not self.busy()
        self.run_btn.setEnabled(ready)
        self.act_check.setEnabled(ready)
        can_save = self.result is not None and self.result.text is not None and not self.busy()
        self.save_btn.setEnabled(can_save)
        self.act_save.setEnabled(can_save)

    def set_banner(self, state, text):
        bg, fg = BANNER[state]
        self.banner.setText(text)
        border = C['border'] if state == 'idle' else bg
        self.banner.setStyleSheet('#Banner { background: %s; color: %s; border: 1px solid %s; }' % (bg, fg, border))

    def set_idle_banner(self):
        d = self.design
        if not d:
            self.set_banner('idle', 'Otvor priečinok s výstupom z návrhu (Gerber + Excellon).')
            return
        x0, y0, x1, y1 = d.board.bounds
        self.set_banner(
            'idle',
            'Doska %s × %s mm · otvory %d · drážky %d · výrezy %d — pokračuj tlačidlom Vypočítať a skontrolovať.'
            % (num(x1 - x0, 1), num(y1 - y0, 1), len(d.holes.round), len(d.holes.slots), len(d.board.interiors)),
        )

    def invalidate(self, why=None):
        """Files or settings changed: whatever was computed (or is being computed) no longer applies."""
        self.epoch += 1
        if self.job:
            self.job.cancelled = True  # stops at its next stage; its result would be dropped anyway
        had = self.result is not None
        self.result = None
        self.table.setRowCount(0)
        self.details.setText('')
        self.sim.set_program(None)
        if had and why and self.design:
            self.set_banner('warn', why)
        elif not self.busy():
            self.set_idle_banner()
        self.update_actions()
        self.redraw()

    # ---------- files
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [Path(u.toLocalFile()) for u in e.mimeData().urls()]
        self.apply_detect([str(q) for p in paths for q in (p.iterdir() if p.is_dir() else [p])])

    def pick_folder(self):
        start = self.store.value('open_dir', str(Path.home()), type=str)
        d = QFileDialog.getExistingDirectory(self, 'Priečinok s výstupom z návrhu', start)
        if d:
            self.store.setValue('open_dir', d)
            self.apply_detect([str(p) for p in Path(d).iterdir()])

    def pick_file(self, key):
        start = str(Path(self.paths_['copper']).parent) if self.paths_['copper'] else ''
        if key == 'drills':
            files, _ = QFileDialog.getOpenFileNames(self, 'Súbory vŕtania (Excellon)', start)
            if not files:
                return
            self.paths_[key] = files
        else:
            what = {'copper': 'meď', 'outline': 'obrys', 'mask': 'nepájivá maska'}[key]
            fn, _ = QFileDialog.getOpenFileName(self, 'Gerber – %s' % what, start)
            if not fn:
                return
            self.paths_[key] = fn
        self.load_design()

    def apply_detect(self, files):
        """Sort a folder's files into layers (None: re-apply the last folder after the side changed)."""
        if files is not None:
            self.detected = gerberio.detect_layers(files)
        d = self.detected
        side = 'copper_top' if self.side() == 'top' else 'copper_bottom'
        other = 'copper_bottom' if side == 'copper_top' else 'copper_top'
        if not d[side] and d[other]:  # only one copper layer in the folder: switch to its side
            self.side_grp.blockSignals(True)
            self.side_grp.button(1 if other == 'copper_top' else 0).setChecked(True)
            self.side_grp.blockSignals(False)
            side = other
        mask = d['mask_top' if side == 'copper_top' else 'mask_bottom'] or ''
        self.paths_ = {
            'copper': d[side] or '',
            'outline': d['outline'] or '',
            'drills': list(d['drills']),
            'mask': mask,
        }
        self.load_design()

    def reload(self):
        if self.paths_['copper'] or self.paths_['outline']:
            self.load_design()

    def load_design(self):
        """Read the chosen files and show the board; computing is a separate step."""
        p = self.paths_
        for key, lbl in self.file_lbl.items():
            names = [Path(x).name for x in ([p[key]] if isinstance(p[key], str) else p[key]) if x]
            empty = {'drills': 'žiadne (doska bez otvorov)', 'mask': 'žiadna (bez masky)'}.get(key, 'nenájdené')
            lbl.setText(', '.join(names) if names else empty)
            lbl.setToolTip('\n'.join([p[key]] if isinstance(p[key], str) else p[key]))
            lbl.setObjectName('FileName' if names or key in ('drills', 'mask') else 'FileMissing')
            lbl.style().unpolish(lbl)
            lbl.style().polish(lbl)
        self.design = None
        error = None
        if not (p['copper'] and p['outline']):
            missing = [n for n, k in (('meď', 'copper'), ('obrys', 'outline')) if not p[k]]
            error = ('idle', 'Chýba súbor: ' + ', '.join(missing))
        else:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                self.design = gerberio.load_design(p['copper'], p['outline'], p['drills'], p['mask'] or None)
            except UserError as e:
                error = ('bad', str(e))
            except Exception as e:
                log.error('loading failed\n%s', traceback.format_exc())
                error = ('bad', 'Súbory sa nedajú načítať (%s). Podrobnosti sú v logu.' % type(e).__name__)
            finally:
                QApplication.restoreOverrideCursor()
        self.sync_stock_fields()
        has_mask = self.design is not None and self.design.mask is not None
        self.fields['with_mask'].setEnabled(has_mask)  # the setting is kept; it applies when a mask file is loaded
        self.invalidate()
        if error:
            self.set_banner(*error)
        folder = Path(p['copper']).parent.name if p['copper'] else ''
        self.setWindowTitle('%s %s%s' % (APP, VERSION, ' — ' + folder if folder else ''))
        self.view.fit()

    # ---------- compute
    def run_bg(self, fn, done, failed, progress=None, tag=None):
        """Run fn(job) in a thread; `done` / `failed` / `progress` must be slots of this window."""
        th = QThread(self)
        jb = Job(fn, tag)
        jb.moveToThread(th)
        th.started.connect(jb.run)
        jb.done.connect(done)
        jb.failed.connect(failed)
        jb.done.connect(th.quit)
        jb.failed.connect(th.quit)
        if progress:
            jb.progress.connect(progress)
        th.finished.connect(self.reap_threads)
        self.threads.append((th, jb))
        th.start()
        return jb

    @Slot()
    def reap_threads(self):
        self.threads = [(th, jb) for th, jb in self.threads if not th.isFinished()]

    def start(self, fn):
        if self.busy() or not self.design:
            return False
        self.bar.setVisible(True)
        self.bar.setValue(0)
        self.set_banner('busy', 'Počítam…')
        self.job = self.run_bg(fn, self.on_done, self.on_fail, self.on_progress, tag=self.epoch)
        self.update_actions()
        return True

    @Slot(str, int)
    def on_progress(self, stage, pct):
        self.stage.setText(STAGES.get(stage, stage))
        self.bar.setValue(pct)

    def compute(self):
        if not self.design:
            return
        tools, prm, stock, anchor = self.settings()
        design, side = self.design, self.side()
        self.start(lambda jb: job.make(design, tools, prm, stock, side, anchor, progress=jb.say))

    def check_external(self):
        if self.busy() or not self.design:
            return
        start = self.store.value('save_dir', str(Path.home()), type=str)
        fn, _ = QFileDialog.getOpenFileNames(
            self, 'G-kód na kontrolu (pri maske oba programy)', start, 'G-code (*.nc *.cnc *.gcode *.ngc);;Všetko (*)'
        )
        if not fn:
            return
        fn = sorted(fn)  # ..._1_... before ..._2_...
        _, prm, _, _ = self.settings()
        design = self.design
        self.start(
            lambda jb: job.check_file(design, fn, prm.thickness, prm.full_clear, prm.copper_thickness, progress=jb.say)
        )

    def job_ended(self, tag):
        """Common end of a compute/check job. Returns False when its result no longer applies."""
        self.job = None
        self.bar.setVisible(False)
        self.update_actions()
        if tag != self.epoch:  # files or settings changed while it ran
            self.stage.setText('Pripravené')
            if not self.result and self.banner.text() == 'Počítam…':
                self.set_idle_banner()
            self.finished.emit(False)
            return False
        return True

    @Slot(object)
    def on_fail(self, payload):
        tag, kind, message = payload
        if not self.job_ended(tag) or kind == 'cancelled':
            return
        self.stage.setText('Chyba')
        if kind == 'user':
            self.set_banner('bad', message)
        else:
            self.set_banner(
                'bad', 'Neočakávaná chyba (%s). Podrobnosti sú v logu: Pomoc → Otvoriť priečinok s logom.' % message
            )
        self.finished.emit(False)

    @Slot(object)
    def on_done(self, payload):
        tag, r = payload
        if not self.job_ended(tag):
            return
        self.result = r
        self.stage.setText('Hotovo')
        self.update_actions()
        self.sim.set_program(r.prog)
        if self.stack.currentIndex() == 1:
            self.sim.ensure_loaded()
        warned = any(s == 'warn' for _, _, s in r.rows)
        if r.source:
            name = Path(r.source).name
            text = (
                'Kontrola súboru %s: bez chýb.'
                if r.ok
                else 'Kontrola súboru %s: nájdené chyby – sú vyznačené v náhľade.'
            )
            self.set_banner('bad' if not r.ok else 'warn' if warned else 'ok', text % name)
        elif not r.ok:
            self.set_banner('bad', 'Nájdené chyby – v náhľade sú vyznačené červenou.')
        elif warned:
            self.set_banner('warn', 'Výsledok je v poriadku, ale pozri upozornenia v tabuľke.')
        else:
            self.set_banner('ok', 'Výsledok je v poriadku – G-kód môžeš uložiť.')
        self.table.setRowCount(len(r.rows))
        for i, (name, value, state) in enumerate(r.rows):
            self.table.setItem(i, 0, QTableWidgetItem(name))
            v = QTableWidgetItem(value)
            v.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(i, 1, v)
            s = QTableWidgetItem(STATE_TEXT[state])
            s.setForeground(QColor(STATE_COLOR[state]))
            self.table.setItem(i, 2, s)
        self.table.resizeRowsToContents()
        self.details.setText('\n'.join(self.detail_lines(r)))
        self.redraw()
        self.view.fit()
        self.finished.emit(True)

    def detail_lines(self, r):
        info = []
        for sh in r.res['shorts']:
            if sh['at']:
                at = 'pri X %s Y %s (súradnice návrhu)' % (num(sh['at'][0], 2), num(sh['at'][1], 2))
            else:
                at = ''
            info.append('Skrat spája %d ostrovy medi %s.' % (sh['islands'], at))
        if len(r.parts) > 1:
            info.append('2 programy: %s.' % ', '.join(
                '%d. %s (%s)' % (n, name, hms(t)) for n, (name, t) in enumerate(r.report['part_times'], 1)))  # fmt: skip
        tools = ', '.join('T%d' % t for _, t in r.prog.events) or '—'
        line = 'Nástroje v poradí %s · odhad času %s vrátane výmen nástrojov' % (tools, hms(r.prog.total))
        if r.stock:
            line += ' · polotovar %s × %s mm' % (num(r.stock[0], 1), num(r.stock[1], 1))
        info.append(line + '.')
        if r.source:
            how = 'hlavičky z1pcb' if r.res['registration'] == 'header' else 'vŕtaných otvorov'
            info.append('Súbor %s, zarovnaný s návrhom podľa %s.' % (r.source, how))
        return info

    # ---------- preview
    def cur_view(self):
        return self.sim.view if self.stack.currentIndex() == 1 else self.view

    def set_mode(self, i):
        self.stack.setCurrentIndex(i)
        self.layer_box.setVisible(i == 0)
        if i == 1:
            self.sim.ensure_loaded()
            self.sim.view.setFocus()  # so that the space bar reaches keyPressEvent below, not a field
        else:
            self.sim.pause()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Space and self.stack.currentIndex() == 1:
            self.sim.toggle()
            return
        super().keyPressEvent(e)

    def layer_toggled(self, button, key, on):
        button.setIcon(swatch(LAYER[key] if on else QColor(C['border'])))
        button.setStyleSheet('' if on else 'color: %s;' % C['muted'])
        self.redraw()

    def frame(self):
        """Where the board sits in the stock: from the result, or what the current settings would give."""
        if self.result:
            return self.result.frame, self.result.stock
        tools, prm, stock, anchor = self.settings()
        place = gcode.Placement(self.design.board.bounds, stock, self.side(), anchor, cam.reach(tools, prm))
        return place, stock

    def redraw(self):
        sc = self.view.scene()
        sc.clear()
        d = self.design
        if not d:
            return
        frame, stock = self.frame()
        on = lambda key: self.show_layer[key].isChecked()
        if on('board'):
            if stock:
                corners = [frame.inverse(p) for p in ((0, 0), (stock[0], 0), (stock[0], stock[1]), (0, stock[1]))]
                pen = QPen(QColor('#6b737c'), 0, Qt.DashLine)
                sc.addPolygon(QPolygonF([QPointF(*c) for c in corners]), pen, QBrush(QColor('#1a1d21')))
            sc.addPath(poly_path(d.board), QPen(QColor('#6b737c'), 0), QBrush(QColor('#23272d')))
        if on('copper'):
            sc.addPath(poly_path(d.copper), QPen(Qt.NoPen), QBrush(LAYER['copper']))
        r = self.result
        if r:
            by_tool = {}
            for tool, pts, _zz in r.geom['paths']:
                kind = r.prog.tools[tool].kind if tool in r.prog.tools else ''
                by_tool.setdefault(5 if kind == 'mask' else tool if tool in (2, 3, 4) else 3, []).append(pts)
            for tool in (4, 2, 3, 5):
                if tool in by_tool and on(tool):
                    c = QColor(LAYER[tool])
                    c.setAlpha(200)
                    sc.addPath(lines_path(by_tool[tool]), QPen(c, 0))
            if on(3):
                for x, y in r.report.get('tab_points', []):
                    sc.addEllipse(QRectF(x - 1.2, y - 1.2, 2.4, 2.4), QPen(QColor('#ffffff'), 0), QBrush(LAYER[3]))
            if on('problem'):
                red = LAYER['problem']
                fill = QColor(red)
                fill.setAlpha(150)
                # only what the check counts as an error: crumbs below its limits would litter the board with red
                res = r.res
                shown = list(r.geom['shorts'])
                if res['copper_hit_mm2'] >= 0.05:
                    shown.append(r.geom['copper_hit'])
                if r.expect.full_clear and res['noncopper_uncut_mm2'] >= 0.5:
                    shown.append(r.geom['uncut'])
                if not r.expect.full_clear and res['thin_strips_mm2'] >= 0.5:
                    shown.append(r.geom['thin'])
                if r.res.get('mask_open_pct', 100) < 95:
                    shown.append(r.geom['mask_left'])
                if r.res.get('mask_extra_mm2', 0) >= 0.05:
                    shown.append(r.geom['mask_extra'])
                for g in shown:
                    if not g.is_empty:
                        sc.addPath(poly_path(g.buffer(0.15)), QPen(red, 0), QBrush(fill))
                for s in r.res['shorts']:
                    if s['at']:
                        sc.addEllipse(QRectF(s['at'][0] - 2, s['at'][1] - 2, 4, 4), QPen(red, 0.25))
        if on('board') and stock:  # machine zero: front-left corner of the stock (mirrored for the bottom side)
            ox, oy = frame.inverse((0, 0))
            white = QPen(QColor('#ffffff'), 0)
            sc.addLine(ox - 3, oy, ox + 3, oy, white)
            sc.addLine(ox, oy - 3, ox, oy + 3, white)
            sc.addEllipse(QRectF(ox - 1.2, oy - 1.2, 2.4, 2.4), white)
            label = sc.addSimpleText('X0 Y0')
            label.setBrush(QColor('#ffffff'))
            label.setFlag(QGraphicsItem.ItemIgnoresTransformations)
            label.setPos(ox + 1.5, oy - 1.5)

    # ---------- save, guide
    def save(self):
        r = self.result
        if not r or r.text is None or self.busy():
            return
        if not r.ok:
            box = QMessageBox(self)
            box.setWindowTitle(APP)
            box.setIcon(QMessageBox.Warning)
            box.setText('Kontrola našla chyby. Tento G-kód môže poškodiť dosku alebo nechať skrat.')
            box.setInformativeText('Naozaj ho chceš uložiť?')
            box.setStandardButtons(QMessageBox.Save | QMessageBox.Cancel)
            box.setDefaultButton(QMessageBox.Cancel)
            if box.exec() != QMessageBox.Save:
                return
        folder = self.store.value('save_dir', str(Path.home()), type=str)
        default = Path(folder) / ('%s_%s.cnc' % (Path(self.paths_['copper']).stem, self.side()))
        title = 'Uložiť G-kód' if len(r.parts) == 1 else 'Uložiť G-kód (2 programy, prípona _1_… a _2_… sa pridá)'
        fn, _ = QFileDialog.getSaveFileName(self, title, str(default), 'G-code (*.cnc)')
        if not fn:
            return
        base = Path(fn)
        names = [base.with_name(base.stem + suffix + (base.suffix or '.cnc')) for suffix, _ in r.parts]
        try:
            for name, (_, text) in zip(names, r.parts):
                gcode.save(text, name)
        except OSError as e:
            QMessageBox.warning(self, APP, 'Súbor sa nepodarilo uložiť:\n%s' % e)
            return
        self.store.setValue('save_dir', str(base.parent))
        self.stage.setText('Uložené: %s' % ', '.join(n.name for n in names))
        log.info('saved %s (ok=%s)', ', '.join(map(str, names)), r.ok)

    def machine_guide(self):
        tools, prm, stock, _ = self.settings()
        names = {
            4: 'plochá fréza Ø %s mm' % num(tools['clear'].diameter, 2),
            3: 'kukuričná fréza Ø %s mm' % num(tools['corn'].diameter, 2),
        }
        names[2] = 'rytec %s° s hrotom %s mm' % (num(2 * tools['vbit'].half_angle, 0), num(tools['vbit'].diameter, 2))
        names[5] = 'nástroj na masku s hrotom %s mm' % num(tools['mask'].diameter, 2)
        r = self.result
        order = [t for _, t in r.prog.events] if r and r.text else ([4, 2, 3] if prm.full_clear else [2, 3])
        steps = [
            'Polotovar %s × %s × %s mm prilep meďou hore na obetnú podložku. Rez ide %s mm pod dosku.'
            % (num(stock[0], 1), num(stock[1], 1), num(prm.thickness, 2), num(prm.through_extra, 1)),
            'Nulový bod X0 Y0 nastav na ľavý predný roh polotovaru, Z0 na povrch medi.',
            'Nástroje v tomto poradí: ' + ', '.join('T%d %s' % (t, names.get(t, '')) for t in order) + '. '
            'Stroj si každý vypýta sám (T… M6) a po výmene zmeria jeho dĺžku. Osi musia byť pred spustením zahomované.',
            'Výškovú mapu (autoleveling) spusti pri odoslaní úlohy z Makera Studio a zmeraj ňou celý polotovar: '
            'mimo zmeranej plochy firmvér Z1 výšku nekoriguje vôbec a okraje by sa rezali v zlej hĺbke. '
            'Meranie nezastavuj; ak sa zastaví, pred ďalším meraním stroj vypni a zapni (chyba firmvéru: sonda '
            'by mohla prejsť po obrobku). Či Studio výškovú mapu spraví aj pri G-kóde z tejto aplikácie, zatiaľ '
            'nie je overené na stroji: bez výškovej mapy izoláciu nefrézuj.',
            'Prvú dosku rob skúšobne a po frézovaní prezvoň susedné spoje multimetrom.',
        ]
        if prm.mask:
            steps.insert(4, MASK_PAUSE + ' Pred 2. programom over na stroji, ako Z1 zmeria pružiaci nástroj T5 '
                         '(pri meraní sa hrot stlačí): zatiaľ neoverené.')  # fmt: skip
        if r and r.text:
            x0, y0, x1, y1 = self.design.board.bounds
            xs, ys = zip(*(r.frame(p) for p in ((x0, y0), (x1, y1))))
            steps.insert(
                2, 'Doska začína %s mm vpravo a %s mm dozadu od nulového bodu.' % (num(min(xs), 1), num(min(ys), 1))
            )
            steps.append('Odhad času: %s vrátane výmen nástrojov.' % hms(r.prog.total))
        box = QMessageBox(self)
        box.setWindowTitle('%s – postup na stroji' % APP)
        box.setTextFormat(Qt.RichText)
        box.setText(
            '<ol style="margin-left:-18px">'
            + ''.join('<li style="margin-bottom:6px">%s</li>' % s for s in steps)
            + '</ol>'
        )
        box.exec()

    def open_log_dir(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(applog.log_dir())))

    def open_licenses(self):
        """The installed app carries every license text in licenses/ (tools/collect_licenses.py)."""
        frozen = getattr(sys, 'frozen', False)
        folder = (
            Path(sys.executable).parent / 'licenses' if frozen else Path(__file__).resolve().parents[1] / 'licenses'
        )
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def about(self):
        QMessageBox.about(
            self,
            APP,
            '<b>%s %s</b><br>G-kód pre Makera Z1 z Gerber a Excellon súborov.<br>'
            'Každý výstup sa pred uložením geometricky skontroluje (meď, skraty, otvory, obrys).<br><br>'
            'Licencia MIT. Používa Qt for Python (LGPL-3.0), GEOS (LGPL-2.1) a ďalšie súčasti; '
            'ich zoznam a licencie: Pomoc → Licencie.<br>'
            '<a href="https://github.com/JoeApo108/z1pcb">github.com/JoeApo108/z1pcb</a>' % (APP, VERSION),
        )

    @Slot(str)
    def on_uncaught(self, text):
        self.stage.setText('Neočakávaná chyba: %s (podrobnosti v logu)' % text)

    # ---------- updates
    def check_updates(self, manual=False):
        self.run_bg(lambda jb: updates.latest(), self.on_update_info, self.on_update_error, tag=manual)

    @Slot(object)
    def on_update_info(self, payload):
        manual, rel = payload
        self.release = rel
        self.update_checked = True
        if rel:
            self.upd_btn.setText('Nová verzia %s – aktualizovať' % rel.version)
            self.upd_btn.setVisible(True)
            if manual or self.auto_update:
                self.offer_update(rel)
        elif manual:
            QMessageBox.information(self, APP, 'Máš najnovšiu verziu (%s).' % VERSION)

    @Slot(object)
    def on_update_error(self, payload):
        manual, kind, message = payload
        self.update_checked = True
        if kind == 'user':  # e.g. a release with an invalid signature: worth knowing even without asking
            self.stage.setText('Aktualizácia: %s' % message)
        if manual:
            QMessageBox.warning(self, APP, 'Kontrola aktualizácií zlyhala:\n%s' % message)
        if self.auto_update:
            QApplication.exit(3)

    def offer_update(self, rel):
        if not rel:
            return
        if sys.platform != 'win32':  # the installer is for Windows; elsewhere open the release page
            QDesktopServices.openUrl(QUrl(rel.page))
            return
        if not self.auto_update:
            box = QMessageBox(self)
            box.setWindowTitle(APP)
            box.setIcon(QMessageBox.Question)
            box.setText('Je dostupná verzia %s (máš %s).' % (rel.version, VERSION))
            box.setInformativeText('Stiahnuť a nainštalovať teraz? Aplikácia sa zavrie a po inštalácii znova spustí.')
            if rel.notes:
                box.setDetailedText(rel.notes)
            box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
            box.setDefaultButton(QMessageBox.Yes)
            if box.exec() != QMessageBox.Yes:
                return
        self.upd_btn.setEnabled(False)
        self.bar.setVisible(True)
        self.bar.setValue(0)
        text = 'Sťahujem verziu %s' % rel.version
        self.stage.setText(text)

        def work(jb):
            return updates.download(rel, lambda p: jb.progress.emit(text, p), lambda: jb.cancelled)

        self.run_bg(work, self.on_update_downloaded, self.on_update_dl_error, self.on_progress)

    @Slot(object)
    def on_update_downloaded(self, payload):
        _, path = payload
        self.stage.setText('Spúšťam inštaláciu…')
        log.info('starting installer %s', path)
        flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        subprocess.Popen(
            [path, '/SILENT', '/SP-', '/NOCANCEL', '/CLOSEAPPLICATIONS'], creationflags=flags, close_fds=True
        )
        QTimer.singleShot(300, QApplication.quit)

    @Slot(object)
    def on_update_dl_error(self, payload):
        _, kind, message = payload
        self.bar.setVisible(False)
        self.upd_btn.setEnabled(True)
        self.stage.setText('Aktualizácia zlyhala')
        if self.auto_update:
            QApplication.exit(4)
        elif kind != 'cancelled':
            QMessageBox.warning(self, APP, 'Aktualizácia zlyhala:\n%s' % message)

    # ---------- shutdown
    def stop_threads(self):
        """Stop background work before the window (and its threads) go away: a running QThread must not be destroyed."""
        self.sim.pause()
        for _, jb in self.threads:
            jb.cancelled = True
        for th, _ in list(self.threads):
            th.quit()
            if not th.wait(15000):  # one geometry step cannot be interrupted; give up only after a long wait
                th.terminate()
                th.wait(2000)
        self.threads = []

    def closeEvent(self, e):
        self.hide()
        self.stop_threads()
        e.accept()


SK_BUTTONS = {
    'OK': 'OK',
    'Save': 'Uložiť',
    'Save All': 'Uložiť všetko',
    'Open': 'Otvoriť',
    '&Yes': 'Án&o',
    'Yes to &All': 'Áno pre &všetko',
    '&No': '&Nie',
    'N&o to All': 'Nie pre vš&etko',
    'Abort': 'Prerušiť',
    'Retry': 'Skúsiť znova',
    'Ignore': 'Ignorovať',
    'Close': 'Zavrieť',
    'Cancel': 'Zrušiť',
    'Discard': 'Zahodiť',
    "Don't Save": 'Neukladať',
    'Help': 'Pomocník',
    'Apply': 'Použiť',
    'Reset': 'Obnoviť',
    'Restore Defaults': 'Obnoviť predvolené',
}


class ButtonTexts(QTranslator):
    """Qt 6 takes the texts of standard dialog buttons from the QPlatformTheme context, which Qt's Slovak
    translation does not cover. None (a null string) passes the question on to the other translators;
    an empty string would end it with an empty text."""

    def translate(self, context, source, disambiguation=None, n=-1):
        return SK_BUTTONS.get(source) if context == 'QPlatformTheme' else None


def install_qt_translation(app):
    """Slovak texts for Qt's own widgets (dialog buttons, context menus). True when Qt's translation was found."""
    import PySide6

    here = Path(PySide6.__file__).parent
    folders = [QLibraryInfo.path(QLibraryInfo.TranslationsPath), here / 'translations', here / 'Qt' / 'translations']
    qt = QTranslator(app)
    found = any(qt.load(QLocale(QLocale.Slovak, QLocale.Slovakia), 'qtbase', '_', str(f)) for f in folders)
    if found:
        app.installTranslator(qt)
    else:
        log.warning('Qt translation qtbase_sk not found in %s', folders)
    buttons = ButtonTexts(app)
    app.installTranslator(buttons)
    app.z1pcb_translators = [qt, buttons]  # the Python objects must live as long as the application
    return found


def update_check(out):
    """--update-check FILE: one real request to GitHub; proves TLS and certificates work in the packaged app."""
    try:
        rel = updates.latest()
        text, code = 'OK: %s' % ('release %s' % rel.version if rel else 'no newer release'), 0
    except urllib.error.HTTPError as e:  # an HTTP answer (404: no release yet) means the TLS connection worked
        text, code = 'OK: HTTPS works, server answered HTTP %d' % e.code, 0
    except Exception as e:
        text, code = 'FAILED: %s: %s' % (type(e).__name__, e), 1
    Path(out).write_text(text + '\n')
    return code


def main(argv=None):
    argv = sys.argv if argv is None else argv
    if '--version' in argv:
        print(VERSION)
        return 0
    if '--update-check' in argv:
        return update_check(argv[argv.index('--update-check') + 1])
    testing = '--selftest' in argv or '--update-test' in argv
    if testing:  # never read or write the user's real settings in a test run
        os.environ.setdefault('Z1PCB_SETTINGS', str(Path(tempfile.mkdtemp(prefix='z1pcb-test-')) / 'settings.ini'))
    QLocale.setDefault(QLocale(QLocale.Slovak, QLocale.Slovakia))  # decimal comma, independent of Windows language
    app = QApplication(argv)
    app.setApplicationName(APP)
    translated = install_qt_translation(app)
    bridge = Bridge()
    applog.setup(bridge.error.emit)
    ico = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1])) / 'assets' / 'z1pcb.png'
    if ico.exists():
        app.setWindowIcon(QIcon(str(ico)))
    apply_theme(app)
    w = Main()
    bridge.error.connect(w.on_uncaught)
    app.aboutToQuit.connect(w.stop_threads)
    w.show()
    if len(argv) > 1 and not argv[1].startswith('--') and Path(argv[1]).is_dir():  # z1pcb <folder>: preload files
        w.apply_detect([str(p) for p in Path(argv[1]).iterdir()])
    if '--update-test' in argv:  # check, download, verify, start the installer silently, quit
        updates.use_test_server(argv[argv.index('--update-test') + 1])
        w.auto_update = True
        w.check_updates()
        QTimer.singleShot(60000, lambda: app.exit(5))
        QTimer.singleShot(15000, lambda: app.exit(6) if w.update_checked and not w.release else None)
        return app.exec()
    if not testing and not os.environ.get('Z1PCB_NO_UPDATE_CHECK'):
        QTimer.singleShot(1500, w.check_updates)
    if '--selftest' in argv:  # load a folder, compute, save a window screenshot, quit
        folder, shot = argv[argv.index('--selftest') + 1], argv[argv.index('--screenshot') + 1]
        if '--isolation-only' in argv:
            w.fields['full'].setChecked(False)
        w.apply_detect([str(p) for p in Path(folder).iterdir()])

        def finish(ok):
            ok = ok and translated and w.result is not None and w.result.ok  # translation: checks the package
            if ok and '--sim' in argv:  # open the simulation tab and seek into the program
                w.mode_grp.button(1).setChecked(True)
                w.set_mode(1)
                w.sim.seek(w.sim.prog.total * float(argv[argv.index('--sim') + 1]))
            app.processEvents()
            w.grab().save(shot)
            app.exit(0 if ok else 1)

        w.finished.connect(finish)
        w.compute()
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
