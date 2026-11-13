"""IR strip board end to end: own G-code must pass; the Makera Studio 0.1.3.0 export shows its known defects."""

import pytest
from conftest import DATA

from z1pcb import cam, gerberio, job


def state(r, label):
    return next(s for name, _, s in r.rows if name.startswith(label))


def test_layers(ir):
    assert len(gerberio.as_polys(ir.copper)) == 61
    assert abs(ir.board.area - 4513.8) < 0.5 and len(ir.board.interiors) == 4
    assert len(ir.holes.round) == 148 and len(ir.holes.slots) == 2
    assert set(ir.hashes) == {'copper', 'outline', 'drills'}


def test_full_clear(ir_full):
    r = ir_full
    assert r.ok, r.rows
    assert r.res['copper_hit_mm2'] < 0.05 and r.res['noncopper_uncut_mm2'] < 0.05
    assert r.res['shorts'] == [] and r.res['holes_under_99pct'] == 0 and r.res['stray_through_mm2'] < 0.05
    assert r.res['cutouts_pct'] == [100.0] * 4
    assert r.prog.used_tools() == [2, 3, 4] and [t for _, t in r.prog.events] == [4, 2, 3]
    assert state(r, 'Súbory návrhu') == 'ok'


def test_isolation_only_removes_thin_strips(ir):
    r = job.make(ir, cam.default_tools(), cam.Params(full_clear=False), (160, 60))
    assert r.ok, r.rows
    assert r.prog.used_tools() == [2, 3] and r.report['links'] == 0  # no cuts across free copper in this mode
    assert r.res['thin_strips_mm2'] < 0.5 and r.res['shorts'] == []
    assert r.res['noncopper_uncut_mm2'] > 1000  # free copper stays


def test_top_side_and_corner_placement(ir):
    r = job.make(ir, cam.default_tools(), cam.Params(full_clear=False), (160, 60), side='top', anchor='corner')
    assert r.ok, r.rows
    assert r.frame.mx == 1.0
    x0, y0, x1, y1 = ir.board.bounds
    assert r.frame((x0, y0)) == pytest.approx((0.8, 0.8))  # board corner sits one cutter reach from the stock corner


def test_faster_than_feeding_down_from_safe_z(ir_full, ir):
    slow = job.make(ir, cam.default_tools(), cam.Params(approach_z=2.0), (160, 60))
    assert ir_full.prog.total < 0.9 * slow.prog.total


def test_studio_export_defects(ir):
    r = job.check_file(ir, DATA / 'studio_0.1.3.0_bottom.nc')
    assert not r.ok
    assert r.res['registration'] == 'drills' and r.res['drill_residual_mm'] < 0.01
    assert len(r.res['shorts']) == 1 and r.res['shorts'][0]['islands'] == 11  # GND + U1 pins 21-30 not isolated
    assert r.res['cutouts_pct'] == [0.0] * 4  # relay slots not cut


def test_short_hops_stay_low_and_nearby_paths_are_joined(ir, ir_full):
    old = job.make(ir, cam.default_tools(), cam.Params(clear_step=0.9, hop_short=0, link_max=0), (160, 60))
    assert ir_full.prog.total < 0.85 * old.prog.total
    assert ir_full.report['links'] > 500
    lifts = lambda r: r.text.count('G0 Z2.000')
    assert lifts(ir_full) < 0.2 * lifts(old)
