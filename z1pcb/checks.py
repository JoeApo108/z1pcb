"""Verify Z1 PCB G-code (ours or Makera Studio's) against the design: copper damage, shorts, holes, outline."""

import math
from collections import Counter
from dataclasses import dataclass, field

import numpy as np
from shapely import LineString, Point, Polygon, STRtree, unary_union

from .errors import UserError
from .fmt import num
from .geom import MIN_STRIP, QS, as_polys, strips
from .gerberio import hole_geom


@dataclass
class Expect:
    """What the G-code is supposed to do; the defaults fit a file from another program."""

    full_clear: bool = True  # all free copper must be gone (else: isolation only)
    outline_min_pct: float = 60.0  # own files: derived from the tab layout
    min_hole_d: float = 0.0  # own files: holes smaller than the cutter come out this big
    small_holes: list = field(default_factory=list)  # diameters of those holes
    narrow_cutouts: int = 0  # cutouts narrower than the cutter (not made)
    inputs: dict = None  # role -> sha256 of the design files now loaded
    copper_thickness: float = 0.035  # copper foil: a cut counts as through it 0.01 mm below
    copper_depth: float = None  # own files: the depth asked for, checked against the foil
    mask: bool = False  # solder mask job: the pads must be scraped free, nothing else


def cuts(prog):
    """Feed moves below the stock surface: XY polylines [tool, shallowest z, points] and plunges (tool, xy, z)."""
    p0, p1 = prog.p0, prog.p1
    below = (prog.feed > 0) & (np.minimum(p0[:, 2], p1[:, 2]) < 0)
    xy = np.hypot(p1[:, 0] - p0[:, 0], p1[:, 1] - p0[:, 1]) > 1e-9
    polylines, plunges, last = [], [], -2
    for i in np.flatnonzero(below):
        tool = int(prog.tool[i])
        if xy[i]:
            zz = max(p0[i, 2], p1[i, 2])
            cur = polylines[-1] if polylines else None
            if last != i - 1 or cur[0] != tool or abs(cur[1] - zz) > 1e-9:
                cur = [tool, zz, [(p0[i, 0], p0[i, 1])]]
                polylines.append(cur)
            cur[2].append((p1[i, 0], p1[i, 1]))
            last = i
        elif p1[i, 2] < p0[i, 2]:
            plunges.append((tool, (p0[i, 0], p0[i, 1]), p1[i, 2]))
    return polylines, plunges


def register(plunges, holes, thickness):
    """Design -> machine X mirror and offset, from through-plunges against drill centres (files without our header)."""
    pts = [p for _, p, z in plunges if z <= -thickness + 0.05]
    centres = [(x, y) for x, y, _, _ in holes.round]
    if not pts or not centres:
        raise UserError('G-kód sa nedá zarovnať s návrhom: nemá hlavičku z1pcb ani otvory vŕtané zanorením.')
    sample = pts[:: max(1, len(pts) // 40)][:40]
    best = None
    for mx in (-1.0, 1.0):
        votes = Counter()
        for px, py in sample:  # a plunge that drills a hole votes for the true offset once
            for hx, hy in centres:
                votes[(round(px - mx * hx, 2), round(py - hy, 2))] += 1
        (tx, ty), n = votes.most_common(1)[0]
        if best is None or n > best[0]:
            best = (n, mx, tx, ty)
    n, mx, tx, ty = best
    if n < max(3, 0.5 * len(sample)):
        raise UserError('G-kód nepatrí k tejto doske: vŕtania nesadnú na otvory z návrhu.')
    tree = STRtree([Point(p) for p in pts])
    dx, dy = [], []
    for hx, hy in centres:  # refine with the holes drilled exactly on their centre
        px, py = pts[tree.nearest(Point(mx * hx + tx, hy + ty))]
        if math.hypot(px - mx * hx - tx, py - hy - ty) < 0.05:
            dx.append(px - mx * hx)
            dy.append(py - hy)
    tx, ty = sum(dx) / len(dx), sum(dy) / len(dy)
    residual = max(math.hypot(u - tx, v - ty) for u, v in zip(dx, dy))
    return {'mx': mx, 'tx': tx, 'ty': ty, 'residual': residual}


def check(prog, copper, board, holes, thickness=1.6, expect=None, want_geom=False, mask=None):
    expect = expect or Expect()
    missing = prog.undefined_tools()
    if missing:
        raise UserError(
            'G-kód neobsahuje rozmery nástrojov (T%s). Kontrola potrebuje hlavičku nástrojov, '
            'akú zapisuje z1pcb alebo Makera Studio.' % ', T'.join(map(str, missing))
        )
    polylines, plunges = cuts(prog)
    reg = prog.transform or register(plunges, holes, thickness)
    mx, tx, ty = reg['mx'], reg['tx'], reg['ty']
    to_g = lambda p: ((p[0] - tx) / mx, p[1] - ty)
    res = {'registration': 'header' if prog.transform else 'drills', 'mx': mx, 'tx': round(tx, 3), 'ty': round(ty, 3)}
    res['drill_residual_mm'] = round(reg.get('residual', 0.0), 4)
    res['inputs'] = {d['role']: d.get('sha256') for d in prog.meta.get('INPUT', []) if 'role' in d}
    res['warnings'] = list(prog.warnings[:5])
    cu = expect.copper_thickness
    res['copper_thickness'] = cu
    cu_cut, through, scraped = [], [], []
    for tool, zz, pts in polylines:
        tl = prog.tools[tool]
        g = LineString([to_g(p) for p in pts])
        if tl.kind == 'mask':  # rides on the surface: takes the mask, not the copper
            scraped.append(g.buffer(tl.radius_at(0), quad_segs=QS))
            continue
        if zz <= -(cu + 0.01):
            cu_cut.append(g.buffer(tl.radius_at(min(-zz, 0.2)), quad_segs=QS))
        if zz <= -thickness + 1e-6:
            through.append(g.buffer(tl.radius_at(thickness), quad_segs=QS))
    for tool, p, zz in plunges:
        if prog.tools[tool].kind == 'mask':
            scraped.append(Point(to_g(p)).buffer(prog.tools[tool].radius_at(0), quad_segs=QS))
            continue
        if zz <= -thickness + 1e-6:
            through.append(Point(to_g(p)).buffer(prog.tools[tool].radius_at(thickness), quad_segs=QS))
    cut, thr = unary_union(cu_cut), unary_union(through)
    cutouts = [Polygon(r) for r in board.interiors]
    expected = unary_union([hole_geom(holes, expect.min_hole_d)] + cutouts)  # through-cut on purpose
    # copper damage outside drilled holes, ignoring a 0.03 mm edge band (geometry tolerance)
    hit = copper.buffer(-0.03, quad_segs=QS).difference(expected.buffer(0.03, quad_segs=QS)).intersection(cut)
    res['copper_hit_mm2'] = round(hit.area, 3)
    # non-copper left standing: beyond 0.15 mm from copper it is unwanted copper; closer it only widens a copper edge
    left = board.difference(copper).difference(cut).difference(thr)
    near = copper.buffer(0.15, quad_segs=QS)
    uncut = left.difference(near)
    res['noncopper_uncut_mm2'] = round(uncut.area, 3)
    edge = [Point(c) for q in as_polys(left.intersection(near)) for c in q.exterior.coords]
    res['copper_edge_widening_max_mm'] = round(max((copper.distance(q) for q in edge), default=0.0), 3)
    thin = Polygon() if expect.full_clear else strips(uncut)  # free-standing copper too narrow to hold
    res['thin_strips_mm2'] = round(thin.area, 3)
    # shorts: leftover copper pieces touching 2+ separate copper islands
    islands = as_polys(copper)
    tree = STRtree(islands) if islands else None
    shorts, short_geoms = [], []
    for comp in as_polys(board.union(copper).difference(cut).difference(thr)) if islands else []:
        touched = [i for i in tree.query(comp) if comp.intersection(islands[i]).area > 1e-4]
        if len(touched) > 1:
            gap = comp.difference(copper)
            at = None if gap.is_empty else tuple(round(v, 2) for v in gap.representative_point().coords[0])
            shorts.append({'islands': len(touched), 'at': at})
            short_geoms.append(gap)
    res['shorts'] = shorts
    # holes / cutouts / outline
    allh = [Point(x, y).buffer(d / 2, quad_segs=QS) for x, y, d, _ in holes.round]
    allh += [LineString([(a, b), (c, e)]).buffer(d / 2, quad_segs=QS) for a, b, c, e, d, _ in holes.slots]
    cov = [thr.intersection(h.buffer(-0.03)).area / max(h.buffer(-0.03).area, 1e-9) for h in allh]
    res['holes_total'] = len(allh)
    res['holes_under_99pct'] = sum(1 for c in cov if c < 0.99)
    res['hole_worst_pct'] = round(100 * min(cov), 1) if cov else None
    stray = thr.intersection(board.buffer(-0.05)).difference(expected.buffer(0.05, quad_segs=QS))
    res['stray_through_mm2'] = round(stray.area, 3)
    res['cutouts_pct'] = [
        round(100 * thr.intersection(c.buffer(-0.03)).area / max(c.buffer(-0.03).area, 1e-9), 1) for c in cutouts
    ]
    outer = Polygon(board.exterior)
    ring = outer.buffer(0.3).difference(outer)
    res['outline_ring_pct'] = round(100 * thr.intersection(ring).area / ring.area, 1)
    cutting = lambda t: prog.tools[t].kind != 'mask'
    zs = [zz for t, zz, _ in polylines if cutting(t)] + [zz for t, _, zz in plunges if cutting(t)]
    res['deepest_z'] = float(min(zs, default=0.0))
    # solder mask: openings scraped free (drilled holes excepted), mask kept everywhere else
    scr = unary_union(scraped) if scraped else Polygon()
    bare, extra = Polygon(), Polygon()
    if expect.mask and mask is not None:
        need = mask.difference(hole_geom(holes, expect.min_hole_d).buffer(0.05, quad_segs=QS))
        bare = need.difference(scr)
        extra = scr.difference(mask.buffer(0.05, quad_segs=QS))
        res['mask_open_pct'] = round(100 * (1 - bare.area / need.area), 1) if need.area else 100.0
        res['mask_extra_mm2'] = round(extra.area, 3)
    if not want_geom:
        return res
    paths = [(tool, [to_g(p) for p in pts], zz) for tool, zz, pts in polylines]
    geom = {'copper_hit': hit, 'uncut': uncut, 'thin': thin, 'shorts': short_geoms, 'paths': paths}
    geom.update(mask_left=bare, mask_extra=extra)
    return res, geom


def verdict(res, expect=None):
    """Rows (label, value, state) for the user and the overall result. state: ok / bad / warn / info."""
    expect = expect or Expect()
    state = lambda good: 'ok' if good else 'bad'
    rows = []
    if expect.copper_depth is not None:
        depth, cu = expect.copper_depth, expect.copper_thickness
        value = '%s mm pri medi %s mm' % (num(depth, 3), num(cu, 3))
        rows.append(('Hĺbka rezu do medi', value, state(depth >= cu + 0.02)))
    rows += [
        ('Zásah do medi mimo otvorov', num(res['copper_hit_mm2'], 3) + ' mm²', state(res['copper_hit_mm2'] < 0.05)),
        ('Skraty medzi spojmi', str(len(res['shorts'])), state(not res['shorts'])),
    ]
    if expect.full_clear:
        v = res['noncopper_uncut_mm2']
        rows.append(('Nevyfrézovaná voľná meď', num(v, 3) + ' mm²', state(v < 0.5)))
    else:
        v = res['thin_strips_mm2']
        rows.append(('Pásiky medi užšie než %s mm' % num(MIN_STRIP, 2), num(v, 2) + ' mm²', state(v < 0.5)))
        rows.append(('Ponechaná voľná meď (len izolácia)', num(res['noncopper_uncut_mm2'], 0) + ' mm²', 'info'))
    done = res['holes_total'] - res['holes_under_99pct']
    rows.append(('Otvory prerezané', '%d / %d' % (done, res['holes_total']), state(res['holes_under_99pct'] == 0)))
    if expect.small_holes:
        sizes = ', '.join('%d× Ø %s' % (n, num(d, 2)) for d, n in sorted(Counter(expect.small_holes).items()))
        rows.append(('Otvory menšie než fréza', '%s → Ø %s mm' % (sizes, num(expect.min_hole_d, 2)), 'warn'))
    v = res['stray_through_mm2']
    rows.append(('Prerezanie mimo otvorov', num(v, 3) + ' mm²', state(v < 0.05)))
    if res['cutouts_pct']:
        value = ', '.join(num(c, 0) + ' %' for c in res['cutouts_pct'])
        rows.append(('Výrezy v doske', value, state(all(c > 99 for c in res['cutouts_pct']))))
    if expect.narrow_cutouts:
        rows.append(('Výrezy užšie než fréza', '%d sa nedá vyrobiť' % expect.narrow_cutouts, 'bad'))
    v = res['outline_ring_pct']
    rows.append(
        (
            'Obrys prerezaný (zvyšok sú mostíky)',
            '%s %% (treba aspoň %s %%)' % (num(v, 1), num(expect.outline_min_pct, 0)),
            state(v >= expect.outline_min_pct),
        )
    )
    if res['inputs'] and expect.inputs:
        same = all(expect.inputs.get(role) == digest for role, digest in res['inputs'].items())
        value = 'zhodné s načítanými' if same else 'G-kód vznikol z iných súborov'
        rows.append(('Súbory návrhu', value, state(same)))
    if 'mask_open_pct' in res:
        v = res['mask_open_pct']
        rows.append(('Plôšky odkryté v maske', num(v, 1) + ' %', state(v >= 95)))
        v = res['mask_extra_mm2']
        rows.append(('Maska odstránená mimo otvorov', num(v, 3) + ' mm²', state(v < 0.05)))
    rows.append(('Najhlbší rez', num(res['deepest_z'], 2) + ' mm', 'info'))
    rows.append(('Rozšírenie okraja medi', 'max ' + num(res['copper_edge_widening_max_mm'], 2) + ' mm', 'info'))
    for w in res.get('warnings', []):
        rows.append(('Upozornenie zo súboru', w, 'warn'))
    return rows, all(s != 'bad' for _, _, s in rows)
