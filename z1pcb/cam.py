"""Toolpaths for one PCB side on a Makera Z1: clear non-copper, isolate, drill/mill holes, slots, outline with tabs."""

import math
from dataclasses import dataclass, field

import numpy as np
from shapely import LineString, MultiLineString, Point, Polygon, unary_union

from .errors import UserError
from .geom import MIN_STRIP, as_polys, strips
from .gerberio import hole_geom
from .tool import Tool

COPPER_DEPTH = {18: 0.05, 35: 0.07, 70: 0.10}  # copper foil (um) -> depth into it: foil + ~0.03 mm
CQ = 16  # buffer quadrant segments for offsets (8 is slower: more vertices to merge with 16-segment pads)


def default_tools():
    return {
        'clear': Tool(4, 'Flat End 2 mm (metal)', 'flat', 2.0, rpm=12000, feed=300, plunge=100),
        'vbit': Tool(2, 'Engraving 30deg x 0.2 mm (metal)', 'vbit', 0.2, 15.0, rpm=12000, feed=500, plunge=200),
        'corn': Tool(3, 'Corn 0.8 mm', 'flat', 0.8, rpm=12000, feed=500, plunge=300),
        'mask': Tool(5, 'Solder Mask Removal 0.3 mm', 'mask', 0.3, 15.0, rpm=6000, feed=400, plunge=200),
    }


@dataclass
class Params:
    thickness: float = 1.6
    copper_thickness: float = 0.035  # copper foil
    copper_depth: float = 0.07  # isolation / clearing depth
    through_extra: float = 0.1  # cut this far below the board (into the spoilboard)
    level_step: float = 0.3  # depth per pass for holes, slots, outline
    vbit_margin: float = 0.02  # V-bit cut edge stays this far from copper
    clear_margin: float = 0.05  # clearing cutter edge stays this far from copper
    clear_step: float = 1.26  # stepover of the clearing cutter as a fraction of its radius (63 % of D, Makera)
    vbit_step: float = 0.18  # stepover of the V-bit rest passes
    tab_count: int = 4
    tab_length: float = 2.8  # along the cutter path
    tab_height: float = 0.5  # material left under a tab
    safe_z: float = 2.0  # XY rapids happen here
    approach_z: float = 0.5  # rapid down to here, feed from here
    hop_short: float = 5.0  # moves up to this long stay at the approach height instead of rising to safe_z
    link_max: float = 5.0  # full clearing: cut straight across to the next path when it is this close and no
    # copper to keep is in reach, instead of lifting
    clearance_z: float = 15.0
    full_clear: bool = True  # remove every non-copper area (needed for mains clearance)
    min_strip: float = 1.2 * MIN_STRIP  # isolation only: free copper strips narrower than this are milled away
    mask: bool = False  # solder mask: second program opens the pads, then holes and outline
    mask_depth: float = 0.2  # into the mask; the spring of the scraper takes up the rest (Makera: 0.2)
    mask_step: float = 0.63  # stepover of the scraper, fraction of its tip


@dataclass
class Block:  # one cutting primitive
    kind: str  # 'path' (one depth), 'levels' (repeat per depth), 'peck'
    points: list
    z: float = 0.0
    levels: list = field(default_factory=list)
    tabs: list = field(default_factory=list)  # (s0, s1) along path, for 'levels'
    tab_top: float = 0.0
    link: bool = False  # reached from the previous block by cutting straight across at depth


@dataclass
class Operation:
    name: str
    tool: Tool
    blocks: list
    part: int = 1  # with a solder mask: 1 = copper, 2 = after the mask (opening, holes, outline)


def reach(tools, prm):
    """How far outside the board outline a cutter edge gets: the stock must be larger by this on every side."""
    r = 2 * tools['corn'].radius_at(0)
    if prm.full_clear:
        r = max(r, 2 * tools['clear'].radius_at(prm.copper_depth))
    return r


def rings(g, min_len=0.1):
    out = []
    for p in as_polys(g):
        for r in [p.exterior] + list(p.interiors):
            if r.length >= min_len:
                out.append(list(r.coords))
    return out


def pocket(region, step, max_iter=400):
    """Contour-parallel offsets; a band too thin for the next offset gets one pass along its middle."""
    loops, todo = [], as_polys(region)
    for _ in range(max_iter):
        if not todo:
            break
        nxt = []
        for p in todo:
            loops += rings(p)
            inner = p.buffer(-step, quad_segs=CQ)
            if inner.is_empty:
                mid = p.buffer(-step / 2, quad_segs=CQ)
                if not mid.is_empty:
                    loops += rings(mid)
            else:
                nxt += as_polys(inner)
        todo = nxt
    return loops


def strip_passes(region):
    """One tight loop along the middle of each narrow region (a V-bit pass there removes the whole strip)."""
    loops = []
    for p in as_polys(region):
        for shrink in (0.13, 0.09, 0.05, 0.0):
            mid = p.buffer(-shrink, quad_segs=CQ)
            if not mid.is_empty:
                loops += rings(mid)
                break
    return loops


def link(blocks, keep, r, max_len):
    """Mark blocks to be reached from the previous one by cutting across instead of lifting: the hop is short
    and the cutter on that line stays clear of the copper to keep. Returns the joining segments."""
    joins = []
    for a, b in zip(blocks, blocks[1:]):
        p, q = a.points[-1], b.points[0]
        if math.dist(p, q) > max_len:
            continue
        line = LineString([p, q]) if math.dist(p, q) > 1e-9 else Point(p)
        b.link = not line.buffer(r, quad_segs=8).intersects(keep)
        if b.link and math.dist(p, q) > 1e-6:
            joins.append((tuple(p), tuple(q)))
    return joins


def sweep(loops, r):
    """Area swept by a cutter of radius r along the loops (one GEOS buffer of the whole collection)."""
    ls = [lp for lp in loops if len(lp) > 1]
    return MultiLineString(ls).buffer(r, quad_segs=8) if ls else Polygon()


def order_paths(paths, closed=True, start=(0.0, 0.0)):
    """Nearest-neighbour order; closed loops are rotated to start nearest the previous end."""
    if not paths:
        return []
    samples = [np.asarray(p[:: max(1, len(p) // 24)], dtype=float) for p in paths]
    owner = np.concatenate([np.full(len(sm), i) for i, sm in enumerate(samples)])
    pts = np.concatenate(samples)
    alive = np.ones(len(paths), bool)
    out, cur = [], np.asarray(start, float)
    for _ in range(len(paths)):
        d = np.hypot(*(pts - cur).T)
        d[~alive[owner]] = np.inf
        i = int(owner[int(np.argmin(d))])
        alive[i] = False
        lp = list(paths[i])
        if closed:
            if lp[0] == lp[-1]:
                lp = lp[:-1]
            a = np.asarray(lp, float)
            j = int(np.argmin(np.hypot(*(a - cur).T)))
            lp = lp[j:] + lp[:j] + [lp[j]]
        elif math.dist(cur, lp[-1]) < math.dist(cur, lp[0]):
            lp = lp[::-1]
        out.append(lp)
        cur = np.asarray(lp[-1], float)
    return out


def point_at(path, s):
    """Point at arc length s along a polyline."""
    for a, b in zip(path, path[1:]):
        d = math.dist(a, b)
        if s <= d and d > 0:
            return (a[0] + (b[0] - a[0]) * s / d, a[1] + (b[1] - a[1]) * s / d)
        s -= d
    return path[-1]


def generate(copper, board, holes, tools=None, prm=None, progress=None, mask=None):
    """Operations in cutting order and a report (counts, tab positions, things the user should know)."""
    tools = tools or default_tools()
    prm = prm or Params()
    say = progress or (lambda stage, pct: None)
    t_clear, t_vbit, t_corn = tools['clear'], tools['vbit'], tools['corn']
    zc = -prm.copper_depth
    r4, rv, r3 = t_clear.radius_at(prm.copper_depth), t_vbit.radius_at(prm.copper_depth), t_corn.diameter / 2
    report = {}

    # --- copper: the clearing cutter takes what it can, the V-bit isolates every copper contour and cleans the rest
    say('clear', 5)
    noncu = board.difference(copper)
    t4_loops = []
    if prm.full_clear:
        centre = board.buffer(r4, quad_segs=CQ).difference(copper.buffer(r4 + prm.clear_margin, quad_segs=CQ))
        t4_loops = pocket(centre, prm.clear_step * r4)
    say('isolate', 35)
    voff = rv + prm.vbit_margin
    iso = rings(copper.buffer(voff, quad_segs=CQ))
    left = noncu.difference(sweep(t4_loops, r4)).difference(sweep(iso, rv))
    say('rest', 50)
    allowed = board.buffer(rv, quad_segs=CQ).difference(copper.buffer(voff, quad_segs=CQ))  # V-bit centre may go here
    if prm.full_clear:  # everything left goes
        rest = left.buffer(-0.01, quad_segs=CQ).buffer(0.01, quad_segs=CQ)  # drop slivers < 0.02 mm
        v_rest = pocket(rest.buffer(rv, quad_segs=CQ).intersection(allowed), prm.vbit_step)
        for _ in range(4):  # clean what offsets missed (thin bands between passes)
            miss = rest.difference(sweep(v_rest, rv)).buffer(-0.005, quad_segs=CQ).buffer(0.005, quad_segs=CQ)
            miss = unary_union([q for q in as_polys(miss) if q.area > 1e-4])
            if miss.is_empty:
                break
            v_rest += rings(miss.buffer(0.4 * rv, quad_segs=CQ).intersection(allowed))
        rest_area = rest.area
    else:  # isolation only: mill away free-standing strips too narrow to survive
        # the band between copper and its groove belongs to the copper, so it is left out
        # holes are cut later, but a hole next to a groove also leaves a strip: take them out first
        drilled = hole_geom(holes, t_corn.diameter)
        free = left.difference(copper.buffer(voff, quad_segs=CQ)).difference(drilled)
        v_rest, rest_area = [], 0.0
        for _ in range(6):  # a pass can leave new slivers where a strip widens: repeat until none are left
            # wider and shorter than what the check reports, so nothing sits right on its limit
            thin = strips(free.difference(sweep(v_rest, rv)), prm.min_strip, min_len=1.5 * MIN_STRIP)
            if thin.area < 0.05:
                break
            rest_area += thin.area
            v_rest += strip_passes(thin.buffer(rv, quad_segs=CQ).intersection(allowed))
    report.update(clear_loops=len(t4_loops), iso_loops=len(iso), rest_loops=len(v_rest), rest_area=rest_area)

    ops, links = [], {}  # tool number -> joining segments
    if t4_loops:
        blocks = [Block('path', lp, z=zc) for lp in order_paths(t4_loops)]
        links[t_clear.number] = link(blocks, copper.buffer(prm.clear_margin - 0.01, quad_segs=CQ), r4, prm.link_max)
        ops.append(Operation('Copper clearing', t_clear, blocks))
    if iso or v_rest:
        blocks = [Block('path', lp, z=zc) for lp in order_paths(iso + v_rest)]
        if prm.full_clear:  # isolation only: a cut across free copper could leave a strip too thin to hold
            links[t_vbit.number] = link(blocks, copper.buffer(prm.vbit_margin - 0.01, quad_segs=CQ), rv, prm.link_max)
        ops.append(Operation('Isolation + rest', t_vbit, blocks))
    report['links'] = sum(len(v) for v in links.values())
    report['link_segments'] = links

    # --- solder mask: a second program scrapes the mask off the pads, before anything is drilled
    after = 1
    if prm.mask:
        if mask is None or mask.is_empty:
            raise UserError('Režim s maskou potrebuje súbor nepájivej masky tejto strany.')
        t_mask = tools['mask']
        rm = t_mask.diameter / 2
        say('mask', 80)
        drilled = hole_geom(holes, t_corn.diameter).buffer(-0.05, quad_segs=CQ)  # comes out with the drilling anyway
        centre = mask.difference(drilled).buffer(-rm, quad_segs=CQ)  # the scraper stays inside the openings
        loops = pocket(centre, prm.mask_step * t_mask.diameter)
        for q in as_polys(mask.difference(drilled)):  # openings smaller than the tip get one touch in the middle
            x0, y0, x1, y1 = q.bounds
            if max(x1 - x0, y1 - y0) <= t_mask.diameter + 0.05 and not centre.intersects(q):
                c = q.representative_point()
                loops.append([(c.x, c.y), (c.x, c.y)])
        blocks = [Block('path', lp, z=-prm.mask_depth) for lp in order_paths(loops)]
        ops.append(Operation('Solder mask opening', t_mask, blocks, part=2))
        report['mask_loops'] = len(blocks)
        after = 2

    # --- holes and slots with the corn bit
    say('holes', 85)
    zt = -(prm.thickness + prm.through_extra)
    levels = []
    z = 0.0
    while z - prm.level_step > zt + 1e-9:
        z -= prm.level_step
        levels.append(round(z, 3))
    levels.append(zt)
    blocks = []
    pecks = [(x, y) for x, y, d, _ in holes.round if d <= t_corn.diameter + 0.05]
    for p in order_paths([[q] for q in pecks], closed=False):
        blocks.append(Block('peck', p, levels=levels))
    milled = []
    for x, y, d, _ in holes.round:
        if d <= t_corn.diameter + 0.05:
            continue
        radii, r = [], d / 2 - r3
        while r > 0.02:
            radii.append(r)
            r -= 0.75 * t_corn.diameter  # rings overlap: step < cutter diameter
        pts = [(x, y)]
        for rr in sorted(radii):
            pts += [
                (x + rr * math.cos(2 * math.pi * k / 48), y + rr * math.sin(2 * math.pi * k / 48)) for k in range(49)
            ]
        pts.append((x, y))
        milled.append(pts)
    for x1, y1, x2, y2, d, _ in holes.slots:
        w = d / 2 - r3
        if w <= 0.01:
            milled.append([(x1, y1), (x2, y2), (x1, y1)])
        else:
            milled.append(rings(LineString([(x1, y1), (x2, y2)]).buffer(w, quad_segs=CQ))[0])
    start = blocks[-1].points[-1] if blocks else (0.0, 0.0)
    order = order_paths([[m[0]] for m in milled], closed=False, start=start)  # visit by start point
    by_start = {}
    for m in milled:
        by_start.setdefault(m[0], []).append(m)
    for q in order:
        blocks.append(Block('levels', by_start[q[0]].pop(), levels=levels))
    if blocks:
        ops.append(Operation('Holes and slots', t_corn, blocks, part=after))
    small = sorted(d for x, y, d, _ in holes.round if d < t_corn.diameter - 0.05)
    small += sorted(d for *_, d, _ in holes.slots if d < t_corn.diameter - 0.05)

    # --- board cutouts (pocketed, no tabs) and outline (outside, tabs), outline last
    say('outline', 93)
    blocks = []
    narrow = 0
    for h in board.interiors:
        region = Polygon(h).buffer(-r3, quad_segs=CQ)
        if region.is_empty:
            narrow += 1  # cutout narrower than the cutter: cannot be made
        for lp in order_paths(pocket(region, min(0.35, r3 * 0.9))):
            blocks.append(Block('levels', lp, levels=levels))
    outer = order_paths([rings(Polygon(board.exterior).buffer(r3, quad_segs=CQ))[0]])[0]
    per = LineString(outer).length
    n_tabs = max(0, min(prm.tab_count, int(0.5 * per / prm.tab_length)))  # tabs never take more than half the outline
    tabs = [
        (per * (k + 0.5) / n_tabs - prm.tab_length / 2, per * (k + 0.5) / n_tabs + prm.tab_length / 2)
        for k in range(n_tabs)
    ]
    tab_top = -(prm.thickness - prm.tab_height)
    blocks.append(Block('levels', outer, levels=levels, tabs=tabs, tab_top=tab_top))
    ops.append(Operation('Cutouts and outline', t_corn, blocks, part=after))
    report.update(
        levels=levels,
        holes=len(holes.round),
        slots=len(holes.slots),
        cutouts=len(board.interiors),
        tabs=n_tabs,
        tab_segments=[(point_at(outer, a), point_at(outer, b)) for a, b in tabs],  # on the cutter path
        tab_height=prm.tab_height,
        cut_radius=r3,
        outline_min_pct=max(0.0, 100 * (1 - n_tabs * prm.tab_length / per) - 5),
        small_holes=small,
        narrow_cutouts=narrow,
        min_hole_d=t_corn.diameter,
    )
    return ops, report
