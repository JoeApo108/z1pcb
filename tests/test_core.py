"""Loading, parsing and the checks on small synthetic cases (one behaviour each)."""

import math

import pytest
from conftest import DATA
from shapely import LineString, Point, box, unary_union

from z1pcb import cam, cli, gcode, gerberio, gparse, job
from z1pcb.errors import UserError

EDGE = DATA / 'ir_lista_cnc-Edge_Cuts.gm1'
SEGMENT = 'X38600000Y-530000D02*\nX114600000Y-530000D01*\n'  # one straight piece of the outer contour


def synth(wd, ht, hole_d=1.0, holes=True, cutout=None):
    """Board wd x ht with one trace between two pads."""
    board = box(0, 0, wd, ht)
    if cutout:
        board = board.difference(cutout)
    pads = [Point(5, ht / 2).buffer(1.2), Point(wd - 5, ht / 2).buffer(1.2)]
    copper = unary_union(pads + [LineString([(5, ht / 2), (wd - 5, ht / 2)]).buffer(0.4)])
    rnd = [(5, ht / 2, hole_d, True), (wd - 5, ht / 2, hole_d, True)] if holes else []
    return gerberio.Design(copper, board, gerberio.Holes(round=rnd), {}, {})


def make(design, **prm):
    tools, p = cam.default_tools(), cam.Params(**prm)
    return job.make(design, tools, p, job.fit_stock(design.board, tools, p))


def row(r, label):
    return next((value, state) for name, value, state in r.rows if name.startswith(label))


# ---------- outline
def test_open_outline_is_an_error(tmp_path):
    text = EDGE.read_text()
    assert SEGMENT in text
    broken = tmp_path / 'open.gm1'
    broken.write_text(text.replace(SEGMENT, ''))
    with pytest.raises(UserError, match='nie je uzavretý'):
        gerberio.load_board(broken)


def test_small_outline_gap_is_closed(tmp_path, ir):
    gapped = tmp_path / 'gap.gm1'
    gapped.write_text(EDGE.read_text().replace('X38600000Y-530000D02*', 'X38610000Y-530000D02*'))  # 0.01 mm gap
    assert gerberio.load_board(gapped).area == pytest.approx(ir.board.area, abs=0.5)


def test_outline_of_another_board_is_an_error(ir):
    with pytest.raises(UserError, match='mimo obrysu'):
        gerberio.validate(ir.copper, box(0, 0, 10, 10), ir.holes)


def test_detect_layers(tmp_path):
    for p in DATA.iterdir():
        (tmp_path / p.name).write_bytes(p.read_bytes())
    (tmp_path / 'notes_outline.json').write_text('{}')
    (tmp_path / 'inner-In1_Cu.gbr').write_text('%TF.FileFunction,Copper,L2,Inr*%\n%FSLAX46Y46*%\n%MOMM*%\nM02*\n')
    d = gerberio.detect_layers(tmp_path.iterdir())
    assert d['copper_bottom'].endswith('B_Cu.gbl') and d['copper_top'] is None
    assert d['outline'].endswith('Edge_Cuts.gm1') and [p[-4:] for p in d['drills']] == ['.drl']


# ---------- generated jobs
@pytest.mark.parametrize('wd, ht, tabs', [(25, 20, 6), (15, 12, 4), (15, 12, 12)])
def test_small_board_outline_passes(wd, ht, tabs):
    r = make(synth(wd, ht), tab_count=tabs)
    assert r.ok, r.rows
    assert r.report['tabs'] * 2.8 <= 0.5 * 2 * (wd + ht) + 3  # tabs never take more than half the outline


def test_hole_smaller_than_cutter_is_a_warning():
    r = make(synth(25, 20, hole_d=0.5))
    assert r.ok, r.rows
    assert row(r, 'Otvory menšie než fréza') == ('2× Ø 0,50 → Ø 0,80 mm', 'warn')


def test_board_without_holes():
    r = make(synth(25, 20, holes=False))
    assert r.ok and r.prog.used_tools() == [2, 3, 4]
    assert row(r, 'Otvory prerezané') == ('0 / 0', 'ok')


def test_cutout_narrower_than_cutter_fails():
    r = make(synth(30, 20, cutout=box(12, 3, 18, 3.5)))
    assert not r.ok and row(r, 'Výrezy užšie než fréza')[1] == 'bad'


def test_stock_too_small():
    d = synth(25, 20)
    with pytest.raises(UserError, match='nezmestí'):
        job.make(d, cam.default_tools(), cam.Params(), (28, 23))


# ---------- G-code reader
def test_modal_gcode_reads_the_same(ir_full):
    lines, out, prev = ir_full.text.splitlines(), [], False
    for ln in lines:
        g1 = ln.startswith('G1 ')
        out.append(ln[3:] if g1 and prev else ln)
        prev = g1
    modal = gparse.parse_lines(out)
    assert len(modal.p0) == len(ir_full.prog.p0) and modal.total == pytest.approx(ir_full.prog.total)
    assert (modal.p1 == ir_full.prog.p1).all()


@pytest.mark.parametrize('line', ['G2 X10 Y0 I5 J0 F100', 'G3 X10 Y0 I5 J0', 'G2 X10 Y0 R5', 'G3 X10 Y0 R5'])
def test_arcs_follow_the_circle(line):
    p = gparse.parse_lines(['G1 X0 Y0 Z-1 F100', line])
    pts = p.p1[1:]
    assert len(pts) > 20 and tuple(pts[-1][:2]) == (10.0, 0.0)
    assert max(abs(math.hypot(x - 5, y) - 5) for x, y, _ in pts) < 1e-6
    top = line.startswith('G2')  # clockwise from (0,0) to (10,0) goes over the top
    assert all((y >= -1e-9) == top or abs(y) < 1e-9 for _, y, _ in pts)


def test_tool_change_forms():
    p = gparse.parse_lines(['T3', 'M6', 'G1 Z-1 F100', 'M6 T4', 'G1 X5', '(T9 M6)', 'G1 X6 ; T8 M6'])
    assert [t for _, t in p.events] == [3, 4] and list(p.tool) == [3, 4, 4]


def test_check_needs_tool_sizes(ir, ir_full, tmp_path):
    f = tmp_path / 'notools.cnc'
    f.write_text('\n'.join(ln for ln in ir_full.text.splitlines() if not ln.startswith(';@MKR|TOOL|')))
    with pytest.raises(UserError, match='rozmery nástrojov'):
        job.check_file(ir, f)


def test_gcode_from_other_design_files_is_flagged(ir, ir_full, tmp_path):
    f = tmp_path / 'own.cnc'
    gcode.save(ir_full.text, f)
    assert row(job.check_file(ir, f), 'Súbory návrhu')[1] == 'ok'
    changed = gerberio.Design(ir.copper, ir.board, ir.holes, ir.files, dict(ir.hashes, copper='0' * 64))
    r = job.check_file(changed, f)
    assert not r.ok and row(r, 'Súbory návrhu')[1] == 'bad'


def test_header_records_how_the_file_was_made(ir_full):
    meta = ir_full.prog.meta
    assert meta['GENERATOR'][0]['version'] and meta['PARAMS'][0]['copper_depth'] == '0.07'
    assert {d['role'] for d in meta['INPUT']} == {'copper', 'outline', 'drills'}


# ---------- command line
def test_cli_make_then_check(tmp_path, capsys):
    files = [
        '--copper',
        str(DATA / 'ir_lista_cnc-B_Cu.gbl'),
        '--outline',
        str(EDGE),
        '--drill',
        str(DATA / 'ir_lista_cnc.drl'),
    ]
    out = tmp_path / 'b.cnc'
    assert cli.main(['make', *files, '--isolation-only', '-o', str(out)]) == 0
    assert cli.main(['check', *files, '--isolation-only', '--gcode', str(out)]) == 0
    assert cli.main(['check', *files, '--gcode', str(out)]) == 1  # full clearing expected, free copper left
    assert 'RESULT: PROBLEMS' in capsys.readouterr().out
