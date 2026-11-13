"""The simulated height map must agree with the design: holes through, copper kept, free copper removed."""

import numpy as np
from shapely import Point

from z1pcb import gerberio, sim


def test_sim_matches_design(ir, ir_full):
    prog = ir_full.prog
    m = sim.Material(prog)
    m.advance_to(len(prog.p0))
    thick = 1.6

    def h(x, y):  # height at a design point
        mx, my = ir_full.frame((x, y))
        return m.h[int(round((my - m.oy) / m.res)), int(round((mx - m.ox) / m.res))]

    assert all(h(x, y) <= -thick for x, y, _, _ in ir.holes.round)
    drilled = gerberio.hole_geom(ir.holes)
    kept = ir.copper.buffer(-0.15).difference(drilled.buffer(0.3))  # copper that must stay
    pads = [p.representative_point() for p in gerberio.as_polys(kept) if p.area > 0.01]
    assert len(pads) > 60 and all(h(p.x, p.y) > -sim.CU for p in pads)
    free = ir.board.difference(ir.copper.buffer(0.3)).difference(drilled.buffer(0.5))
    rng = np.random.default_rng(1)
    x0, y0, x1, y1 = free.bounds
    n = ok = 0
    while n < 300:
        p = Point(rng.uniform(x0, x1), rng.uniform(y0, y1))
        if free.contains(p):
            n += 1
            ok += h(p.x, p.y) <= -sim.CU + 1e-4
    assert ok == n


def test_map_covers_only_what_is_cut(ir_full):
    m = sim.Material(ir_full.prog)
    x, y, w, hgt = m.rect()
    assert w < 160 and hgt < 60 and m.h.nbytes < 20e6
    assert 2 <= m.max_snaps <= 12


def test_seek_back_restores_the_same_state(ir_full):
    prog = ir_full.prog
    m = sim.Material(prog)
    k = len(prog.p0) // 3
    m.advance_to(k)
    ref = m.h.copy()
    m.advance_to(len(prog.p0) // 2)
    m.advance_to(k)
    assert (m.h == ref).all()
