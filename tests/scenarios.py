"""GUI scenarios that used to abort the process; run one per process: python tests/scenarios.py <name>."""

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ['Z1PCB_SETTINGS'] = str(Path(tempfile.mkdtemp()) / 'settings.ini')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from z1pcb import gui  # noqa: E402

name = sys.argv[1]
app = QApplication(sys.argv)
w = gui.Main()
app.aboutToQuit.connect(w.stop_threads)
w.show()
w.apply_detect([str(p) for p in (Path(__file__).parent / 'data' / 'ir_lista').iterdir()])
w.compute()
assert w.busy()
if name == 'close_while_computing':
    QTimer.singleShot(300, w.close)
elif name == 'quit_while_computing':
    QTimer.singleShot(300, app.quit)
elif name == 'second_job_while_computing':
    QTimer.singleShot(300, w.check_external)  # returns at once: a job is running
    QTimer.singleShot(400, lambda: w.start(lambda jb: None))
    w.finished.connect(lambda ok: app.exit(0 if ok and w.result and w.result.ok else 1))
sys.exit(app.exec())
