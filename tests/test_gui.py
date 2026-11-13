"""The window: a result on screen always belongs to the files and settings on screen."""

import subprocess
import sys
import time
from pathlib import Path

import pytest
from conftest import DATA, fake_makera_db
from PySide6.QtCore import QLocale, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractSlider, QApplication, QFileDialog, QMessageBox

from z1pcb import gui, theme

FILES = [str(p) for p in DATA.iterdir() if p.suffix != '.nc']


@pytest.fixture(scope='session')
def app():
    QLocale.setDefault(QLocale(QLocale.Slovak, QLocale.Slovakia))
    app = QApplication.instance() or QApplication([])
    theme.apply_theme(app)
    return app


@pytest.fixture
def win(app, tmp_path, monkeypatch):
    monkeypatch.setenv('Z1PCB_SETTINGS', str(tmp_path / 'settings.ini'))
    w = gui.Main()
    w.show()
    w.activateWindow()
    w.fields['full'].setChecked(False)  # isolation only: the quick job
    w.apply_detect(FILES)
    yield w
    w.close()


def wait(app, cond, seconds=120):
    end = time.time() + seconds
    while not cond() and time.time() < end:
        app.processEvents()
        time.sleep(0.01)
    assert cond()


def computed(app, w):
    w.compute()
    wait(app, lambda: not w.busy())
    assert w.result and w.result.ok, w.banner.text()
    return w.result


def test_board_shows_before_computing(win):
    assert win.design and win.result is None and win.run_btn.isEnabled() and not win.save_btn.isEnabled()
    assert 'Doska 140,0 × 40,9 mm' in win.banner.text()
    assert (win.fields['stock_w'].value(), win.fields['stock_h'].value()) == (142.0, 43.0)  # follows the board
    assert not win.fields['stock_w'].isEnabled()


@pytest.mark.parametrize('field, value', [('depth', 0.09), ('stock_auto', False), ('t_vtip', 0.3), ('feed_vbit', 400)])
def test_changing_a_setting_drops_the_result(app, win, field, value):
    computed(app, win)
    assert win.save_btn.isEnabled()
    w = win.fields[field]
    w.setChecked(value) if isinstance(value, bool) else w.setValue(value)
    assert win.result is None and not win.save_btn.isEnabled() and win.table.rowCount() == 0
    assert 'vypočítaj znova' in win.banner.text()


def test_files_changed_while_computing_drop_the_result(app, win):
    win.compute()
    assert win.busy()
    win.apply_detect([])
    wait(app, lambda: not win.busy())
    assert win.result is None and not win.save_btn.isEnabled() and not win.run_btn.isEnabled()
    assert 'Chýba súbor' in win.banner.text()


def test_only_one_job_at_a_time(app, win):
    win.compute()
    first = win.job
    assert win.start(lambda jb: None) is False and win.job is first and not win.act_check.isEnabled()
    wait(app, lambda: not win.busy())
    assert win.result.ok


def test_decimal_dot_and_comma(win):
    w = win.fields['depth']
    for typed in ('0.08', '0,09'):
        w.setFocus()
        w.selectAll()
        QTest.keyClicks(w, typed)
        QTest.keyClick(w, Qt.Key_Tab)
        assert w.value() == pytest.approx(float(typed.replace(',', '.')))


def test_settings_survive_a_restart(app, win):
    win.fields['depth'].setValue(0.11)
    win.fields['anchor'].setCurrentIndex(1)
    again = gui.Main()
    assert again.fields['depth'].value() == pytest.approx(0.11) and again.fields['anchor'].currentData() == 'corner'
    assert again.fields['full'].isChecked() is False
    again.close()


def test_copper_foil_sets_the_depth(app, win):
    win.fields['copper_um'].setCurrentIndex(win.fields['copper_um'].findData('70'))
    assert win.fields['depth'].value() == pytest.approx(0.10) and win.result is None
    again = gui.Main()
    assert again.fields['copper_um'].currentData() == '70' and again.fields['depth'].value() == pytest.approx(0.10)
    again.close()


def test_board_without_drill_file(win):
    win.apply_detect([f for f in FILES if not f.endswith('.drl')])
    assert win.design and len(win.design.holes.round) == 0 and win.run_btn.isEnabled()
    assert 'bez otvorov' in win.file_lbl['drills'].text()


def test_broken_outline_is_explained(win, tmp_path):
    text = (DATA / 'ir_lista_cnc-Edge_Cuts.gm1').read_text()
    bad = tmp_path / 'x-Edge_Cuts.gm1'
    bad.write_text(text.replace('X38600000Y-530000D02*\nX114600000Y-530000D01*\n', ''))
    win.apply_detect([f for f in FILES if 'Edge' not in f] + [str(bad)])
    assert win.design is None and not win.run_btn.isEnabled() and 'nie je uzavretý' in win.banner.text()


def test_saving_a_failed_result_needs_confirmation(app, win, monkeypatch, tmp_path):
    r = computed(app, win)
    r.ok = False
    asked = []
    monkeypatch.setattr(QMessageBox, 'exec', lambda box: asked.append(box.text()) or QMessageBox.Cancel)
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **k: (str(tmp_path / 'out.cnc'), ''))
    win.save()
    assert asked and not (tmp_path / 'out.cnc').exists()
    r.ok = True
    win.save()
    assert (tmp_path / 'out.cnc').read_bytes().count(b'\r\n') > 1000


def test_simulation_space_bar_and_timeline_click(app, win):
    computed(app, win)
    win.fields['depth'].setFocus()  # the usual case: a field was edited last
    QTest.mouseClick(win.mode_grp.button(1), Qt.LeftButton)
    assert win.sim.prog is not None and app.focusWidget() is win.sim.view
    QTest.keyClick(app.focusWidget(), Qt.Key_Space)
    assert win.sim.playing
    QTest.keyClick(app.focusWidget(), Qt.Key_Space)
    assert not win.sim.playing
    win.sim.seek(0.0)
    win.sim.slider.triggerAction(QAbstractSlider.SliderPageStepAdd)  # what a click on the track does
    wait(app, lambda: win.sim.t > 0, 5)
    assert win.sim.t == pytest.approx(win.sim.prog.total * win.sim.slider.value() / 10000, rel=0.01)


def test_result_matches_new_computation_in_simulation(app, win):
    first = computed(app, win).prog.total
    QTest.mouseClick(win.mode_grp.button(1), Qt.LeftButton)
    assert win.sim.prog.total == first
    win.fields['full'].setChecked(True)
    assert win.sim.prog is None
    second = computed(app, win).prog.total
    assert win.sim.prog.total == second != first


@pytest.mark.parametrize('name', ['close_while_computing', 'quit_while_computing', 'second_job_while_computing'])
def test_no_abort(name):
    p = subprocess.run(
        [sys.executable, str(Path(__file__).parent / 'scenarios.py'), name], capture_output=True, timeout=300
    )
    assert p.returncode == 0, p.stderr.decode()[-500:]


def test_library_tool_fills_size_and_cutting_data(app, tmp_path, monkeypatch):
    monkeypatch.setenv('Z1PCB_SETTINGS', str(tmp_path / 'settings.ini'))
    monkeypatch.setenv('Z1PCB_MAKERA_DB', str(fake_makera_db(tmp_path / 'lib.db')))
    w = gui.Main()
    f = w.fields
    assert f['lib_vbit'].currentData() == 'vbit:v2' and f['lib_corn'].currentData() == 'corn:c8'  # defaults recognised
    f['lib_corn'].setCurrentIndex(f['lib_corn'].findData('corn:c6'))
    assert (f['t_corn'].value(), f['feed_corn'].value(), f['plunge_corn'].value()) == (0.6, 450, 250)
    assert f['level_step'].value() == pytest.approx(0.25)
    tools, prm, _, _ = w.settings()
    assert tools['corn'].name == '3.175*0.6*3mm Corn' and prm.level_step == pytest.approx(0.25)
    f['feed_corn'].setValue(400)  # changed by hand: no longer the library tool
    assert f['lib_corn'].currentData() == '' and w.settings()[0]['corn'].name == 'Corn 0.6 mm'
    f['lib_clear'].setCurrentIndex(f['lib_clear'].findData('clear:{own}'))
    assert (f['t_clear'].value(), f['rpm_clear'].value(), f['clear_pct'].value()) == (1.0, 11000, 60)
    assert w.settings()[1].clear_step == pytest.approx(1.2)
    w.close()


def test_without_makera_studio_values_are_typed_by_hand(win):
    assert not win.fields['lib_corn'].isEnabled() and win.fields['lib_corn'].count() == 1
    assert win.settings()[0]['corn'].name == 'Corn 0.8 mm'


def test_qt_dialog_buttons_in_slovak(app):
    from PySide6.QtWidgets import QMessageBox

    assert gui.install_qt_translation(app)
    box = QMessageBox()
    box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
    assert box.button(QMessageBox.Yes).text().replace('&', '') == 'Áno'
    assert box.button(QMessageBox.No).text().replace('&', '') == 'Nie'
    from PySide6.QtCore import QCoreApplication

    assert QCoreApplication.translate('QMessageBox', 'Show Details...').startswith('Zobraziť')  # Qt's own Slovak file
