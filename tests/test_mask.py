"""Solder mask jobs: copper first, then (after the mask) pads opened, then holes and outline."""

import pytest
from conftest import DATA, fake_makera_db
from shapely.affinity import translate

from z1pcb import cam, cli, gcode, gerberio, gparse, job, makera_lib, sim
from z1pcb.errors import UserError
from z1pcb.tool import Tool

MASK = DATA / 'ir_lista_cnc-B_Mask.gbs'


@pytest.fixture(scope='module')
def masked():
    d = gerberio.load_design(
        DATA / 'ir_lista_cnc-B_Cu.gbl', DATA / 'ir_lista_cnc-Edge_Cuts.gm1', [DATA / 'ir_lista_cnc.drl'], MASK
    )
    return d, job.make(d, cam.default_tools(), cam.Params(mask=True), (160, 60))


def row(r, label):
    return next((value, state) for name, value, state in r.rows if name.startswith(label))


def test_two_programs_in_the_right_order(masked):
    d, r = masked
    assert r.ok, r.rows
    assert [s for s, _ in r.parts] == ['_1_med', '_2_maska_otvory_obrys']
    first, second = (gparse.parse_text(t) for _, t in r.parts)
    assert [t for _, t in first.events] == [4, 2] and [t for _, t in second.events] == [5, 3]  # no drilling first
    assert second.meta['PART'][0] == {'n': '2', 'of': '2'} and second.tools[5].kind == 'mask'
    assert row(r, 'Plôšky odkryté v maske')[1] == 'ok' and row(r, 'Maska odstránená mimo otvorov')[1] == 'ok'
    assert row(r, 'Zásah do medi')[1] == 'ok'  # the scraper is not counted as cutting copper


def test_scraper_leaves_copper_in_the_simulation(masked):
    d, r = masked
    second = gparse.parse_text(r.parts[1][1])
    m = sim.Material(second)
    m.advance_to(int((second.tool == 5).nonzero()[0].max()) + 1)  # every scraper move, nothing drilled yet
    assert m.h.min() == 0.0


def test_missing_and_extra_scraping_are_reported(masked):
    d, r = masked
    tools, prm = cam.default_tools(), cam.Params(mask=True)
    ops, _ = cam.generate(d.copper, d.board, d.holes, tools, prm, mask=d.mask)
    op = next(o for o in ops if o.tool.kind == 'mask')
    op.blocks = op.blocks[: len(op.blocks) // 2]
    text = gcode.render(ops, r.frame, prm, d.hashes)
    exp = job.checks.Expect(mask=True)
    res = job.checks.check(gparse.parse_text(text), d.copper, d.board, d.holes, expect=exp, mask=d.mask)
    assert res['mask_open_pct'] < 80
    shifted = translate(d.mask, 0.5, 0)  # the scraper now also works where the mask must stay
    res = job.checks.check(r.prog, d.copper, d.board, d.holes, expect=exp, mask=shifted)
    assert res['mask_extra_mm2'] > 1


def test_mask_layer_is_found_and_checked(tmp_path, ir):
    for p in DATA.iterdir():
        (tmp_path / p.name).write_bytes(p.read_bytes())
    found = gerberio.detect_layers(tmp_path.iterdir())
    assert found['mask_bottom'].endswith('B_Mask.gbs') and found['mask_top'] is None
    far = tmp_path / 'far.gbs'
    lines = [('X9' + ln[1:]).replace('Y-', 'Y-9') if ln.startswith('X') else ln for ln in MASK.read_text().split('\n')]
    far.write_text('\n'.join(lines))  # every coordinate moved far off the board
    with pytest.raises(UserError, match='mimo obrysu'):
        gerberio.load_design(DATA / 'ir_lista_cnc-B_Cu.gbl', DATA / 'ir_lista_cnc-Edge_Cuts.gm1', [], far)
    with pytest.raises(UserError, match='masky'):
        cam.generate(ir.copper, ir.board, ir.holes, prm=cam.Params(mask=True))


def test_studio_mask_remover_is_recognised():
    tl = Tool.from_header(
        ';@MKR|TOOL|number=5|name=3.175mm Solder Mask Removal|type=Engraving|diameter=3.175|tipdiameter=0.3|halfAngle=15'
    )
    assert tl.kind == 'mask' and tl.radius_at(0.2) == pytest.approx(0.15)


def test_mask_remover_in_the_library(tmp_path):
    lib = makera_lib.load(fake_makera_db(tmp_path / 'lib.db'))
    (t,) = lib.for_role('mask')
    assert (t.diameter, t.rpm, t.feed, t.plunge, t.step_down) == (0.3, 6000, 400, 200, 0.2)


def test_cli_writes_and_checks_both_programs(tmp_path):
    files = ['--copper', str(DATA / 'ir_lista_cnc-B_Cu.gbl'), '--outline', str(DATA / 'ir_lista_cnc-Edge_Cuts.gm1')]
    files += ['--drill', str(DATA / 'ir_lista_cnc.drl'), '--mask', str(MASK), '--isolation-only']
    assert cli.main(['make', *files, '-o', str(tmp_path / 'b.cnc')]) == 0
    out = sorted(p.name for p in tmp_path.glob('b_*.cnc'))
    assert out == ['b_1_med.cnc', 'b_2_maska_otvory_obrys.cnc']
    assert cli.main(['check', *files, '--gcode', *(str(tmp_path / n) for n in out)]) == 0
