"""Read Gerber copper/outline and Excellon drills into shapely geometry (mm, Gerber frame)."""

import hashlib
import math
import re
import warnings
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from gerbonara import ExcellonFile, GerberFile
from gerbonara.graphic_primitives import Arc, ArcPoly, Circle, Line, Rectangle
from gerbonara.utils import MM
from shapely import LineString, Point, Polygon, STRtree, box, unary_union
from shapely.affinity import rotate
from shapely.ops import polygonize

from .errors import UserError
from .geom import QS, as_polys  # noqa: F401  (as_polys is part of this module's interface)

ARC_STEP = 0.05  # max chord length for arcs, mm
SNAP = 0.02  # outline ends closer than this are joined, mm


def arc_points(x1, y1, x2, y2, cx, cy, clockwise):
    r = math.hypot(x1 - cx, y1 - cy)
    a0, a1 = math.atan2(y1 - cy, x1 - cx), math.atan2(y2 - cy, x2 - cx)
    d = a1 - a0
    if clockwise and d >= 0:
        d -= 2 * math.pi
    if not clockwise and d <= 0:
        d += 2 * math.pi
    if abs(x1 - x2) < 1e-9 and abs(y1 - y2) < 1e-9:
        d = -2 * math.pi if clockwise else 2 * math.pi
    n = max(2, int(abs(d) * r / ARC_STEP) + 1)
    pts = [(cx + r * math.cos(a0 + d * k / n), cy + r * math.sin(a0 + d * k / n)) for k in range(n + 1)]
    pts[0], pts[-1] = (x1, y1), (x2, y2)  # exact ends, so outlines close
    return pts


def arcpoly_points(p):
    """ArcPoly outline -> list of points (arcs sampled)."""
    pts = []
    n = len(p.outline)
    for i in range(n):
        a, b = p.outline[i], p.outline[(i + 1) % n]
        arc = p.arc_centers[i] if i < len(p.arc_centers) else None
        if arc is None:
            pts.append(a)
        else:
            clockwise, (cx, cy) = arc
            pts += arc_points(a[0], a[1], b[0], b[1], cx, cy, clockwise)[:-1]
    return pts


def prim_geom(p):
    if isinstance(p, Circle):
        return Point(p.x, p.y).buffer(p.r, quad_segs=QS)
    if isinstance(p, Line):
        if p.width <= 0:
            return None
        return LineString([(p.x1, p.y1), (p.x2, p.y2)]).buffer(p.width / 2, quad_segs=QS)
    if isinstance(p, Arc):
        if p.width <= 0:
            return None
        pts = arc_points(p.x1, p.y1, p.x2, p.y2, p.cx, p.cy, p.clockwise)
        return LineString(pts).buffer(p.width / 2, quad_segs=QS)
    if isinstance(p, Rectangle):
        r = box(p.x - p.w / 2, p.y - p.h / 2, p.x + p.w / 2, p.y + p.h / 2)
        return rotate(r, p.rotation, origin=(p.x, p.y), use_radians=True) if p.rotation else r
    if isinstance(p, ArcPoly):
        pts = arcpoly_points(p)
        return Polygon(pts).buffer(0) if len(pts) >= 3 else None
    raise UserError('Gerber obsahuje nepodporovaný prvok (%s).' % type(p).__name__)


def load_copper(path):
    """Copper layer as one (Multi)Polygon. Dark/clear polarity applied in file order."""
    g = GerberFile.open(path)
    acc, batch, dark = Polygon(), [], True

    def flush():
        nonlocal acc, batch
        if batch:
            u = unary_union(batch)
            acc = acc.union(u) if dark else acc.difference(u)
        batch = []

    for o in g.objects:
        if o.polarity_dark != dark:
            flush()
            dark = o.polarity_dark
        for p in o.to_primitives(MM):
            if p.polarity_dark != dark:  # primitive polarity inside an aperture macro
                flush()
                dark = p.polarity_dark
            geom = prim_geom(p)
            if geom is not None and not geom.is_empty:
                batch.append(geom)
    flush()
    return acc.buffer(0)


def outline_lines(path):
    """Outline layer centrelines (stroke width ignored)."""
    g = GerberFile.open(path)
    lines = []
    for o in g.objects:
        for p in o.to_primitives(MM):
            if isinstance(p, Line):
                lines.append([(p.x1, p.y1), (p.x2, p.y2)])
            elif isinstance(p, Arc):
                lines.append(arc_points(p.x1, p.y1, p.x2, p.y2, p.cx, p.cy, p.clockwise))
            elif isinstance(p, ArcPoly):
                pts = arcpoly_points(p)
                lines.append(pts + pts[:1])
            elif isinstance(p, Circle):
                lines.append(list(Point(p.x, p.y).buffer(p.r, quad_segs=QS).exterior.coords))
    return [ln for ln in lines if len(ln) >= 2]


def snap_ends(lines, tol=SNAP):
    """Join line ends that lie within `tol` of each other (CAD exports leave gaps of a few um)."""
    ends = [ln[k] for ln in lines for k in (0, -1)]
    pts = [Point(e) for e in ends]
    tree = STRtree(pts)
    rep = list(range(len(ends)))

    def find(i):
        while rep[i] != i:
            rep[i] = rep[rep[i]]
            i = rep[i]
        return i

    for i, p in enumerate(pts):
        for j in tree.query(p.buffer(tol)):
            if p.distance(pts[j]) <= tol:
                rep[find(int(j))] = find(i)
    out = []
    for n, ln in enumerate(lines):
        ln = list(ln)
        ln[0], ln[-1] = ends[find(2 * n)], ends[find(2 * n + 1)]
        out.append(ln)
    return out


def open_ends(linework):
    """Points where exactly one line ends (a gap in an outline, or a stray line)."""
    deg = Counter()
    for ln in getattr(linework, 'geoms', [linework]):
        c = list(ln.coords)
        for q in (c[0], c[-1]):
            deg[(round(q[0], 4), round(q[1], 4))] += 1
    return sorted(q for q, n in deg.items() if n == 1)


def load_board(path):
    """Board area: largest closed outline minus the closed outlines inside it (cutouts)."""
    lines = outline_lines(path)
    if not lines:
        raise UserError('Súbor obrysu %s neobsahuje žiadne čiary.' % Path(path).name)
    work = unary_union([LineString(ln) for ln in snap_ends(lines)])
    polys = sorted(polygonize(work), key=lambda p: -p.area)
    gaps = open_ends(work)
    x0, y0, x1, y1 = work.bounds
    whole = polys and all(abs(a - b) < 0.5 for a, b in zip(polys[0].bounds, (x0, y0, x1, y1)))
    if not whole:  # the biggest closed contour does not span the drawn outline: the outer contour is open
        where = ', '.join('X %.2f Y %.2f' % q for q in gaps[:4]) or 'neznáme miesto'
        raise UserError('Obrys dosky nie je uzavretý. Voľný koniec čiary: %s.' % where)
    outer = Polygon(polys[0].exterior)
    holes = [Polygon(p.exterior) for p in polys[1:] if outer.contains(p.representative_point())]
    holes = [h for h in holes if not any(h is not o and o.contains(h) for o in holes)]
    return outer.difference(unary_union(holes)) if holes else outer


@dataclass
class Holes:
    round: list = field(default_factory=list)  # (x, y, d, plated)
    slots: list = field(default_factory=list)  # (x1, y1, x2, y2, d, plated)


def load_drills(paths):
    h = Holes()
    for path in paths:
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', SyntaxWarning)  # KiCad drill header quirks
            e = ExcellonFile.open(path)
        for o in e.objects:
            plated = getattr(o, 'plated', None)
            for p in o.to_primitives(MM):
                if isinstance(p, Circle):
                    h.round.append((p.x, p.y, 2 * p.r, plated))
                elif isinstance(p, Line):
                    h.slots.append((p.x1, p.y1, p.x2, p.y2, p.width, plated))
    return h


def hole_geom(h, min_d=0.0):
    """Area of all holes and slots; holes smaller than `min_d` count as `min_d` (cutter diameter)."""
    g = [Point(x, y).buffer(max(d, min_d) / 2, quad_segs=QS) for x, y, d, _ in h.round]
    g += [LineString([(x1, y1), (x2, y2)]).buffer(max(d, min_d) / 2, quad_segs=QS) for x1, y1, x2, y2, d, _ in h.slots]
    return unary_union(g) if g else Polygon()


@dataclass
class Design:
    copper: object
    board: object
    holes: Holes
    files: dict  # role -> list of paths
    hashes: dict  # role -> sha256 over the role's files


def file_hash(paths):
    h = hashlib.sha256()
    for p in sorted(map(str, paths)):
        h.update(Path(p).read_bytes())
    return h.hexdigest()


def validate(copper, board, holes):
    """The outline must belong to this copper and these holes (catches a wrong or broken outline file)."""
    near = board.buffer(1.0)
    outside = [1 for x, y, _, _ in holes.round if not near.contains(Point(x, y))]
    if outside:
        raise UserError(
            '%d z %d otvorov leží mimo obrysu dosky. Obrys nepatrí k tomuto vŕtaniu alebo nie je celý.'
            % (len(outside), len(holes.round))
        )
    if not copper.is_empty and copper.difference(near).area > 0.05 * copper.area:
        raise UserError(
            '%.0f %% medi leží mimo obrysu dosky. Obrys nepatrí k tejto medi alebo nie je celý.'
            % (100 * copper.difference(near).area / copper.area)
        )


def load_design(copper, outline, drills=()):
    """Load and cross-check one board side. `drills` may be empty (board without holes)."""
    if not copper or not outline:
        raise UserError('Chýba súbor medi alebo obrysu.')
    drills = [d for d in drills if d]
    cu, bd, hl = load_copper(copper), load_board(outline), load_drills(drills)
    validate(cu, bd, hl)
    files = {'copper': [copper], 'outline': [outline], 'drills': drills}
    return Design(cu, bd, hl, files, {k: file_hash(v) for k, v in files.items() if v})


def detect_layers(paths):
    """Sort fabrication files: X2 FileFunction first, then common KiCad/EasyEDA/Altium/Eagle names."""
    found = {'copper_top': None, 'copper_bottom': None, 'outline': None, 'drills': []}
    for p in sorted(map(Path, paths)):
        if not p.is_file():
            continue
        try:
            with open(p, 'rb') as fh:  # only the header: the folder may hold large unrelated files
                head = fh.read(4000).decode('latin-1')
        except OSError:
            continue
        low = p.name.lower()
        ff = re.search(r'TF\.FileFunction,([^*\n]+)', head)
        ff = ff.group(1).lower() if ff else ''
        if head.lstrip().startswith('M48') or re.search(r'\.(drl|xln|exc)$', low):
            found['drills'].append(str(p))
            continue
        if '%FS' not in head and '%MO' not in head:  # not a Gerber file (json, pdf, png, job...)
            continue
        if ff.startswith('profile') or re.search(r'edge[._]?cuts|\.gm1$|\.gko$|\.gml$|outline', low):
            found['outline'] = found['outline'] or str(p)
            continue
        if ff.startswith('copper'):
            if ',inr' in ff:
                continue  # inner layer of a multilayer board
            side = 'copper_bottom' if ',bot' in ff else 'copper_top'
            found[side] = found[side] or str(p)
            continue
        if re.search(r'b[._]cu|\.gbl$|bottom.?(copper|layer)|copper.?bottom', low):
            found['copper_bottom'] = found['copper_bottom'] or str(p)
            continue
        if re.search(r'f[._]cu|\.gtl$|top.?(copper|layer)|copper.?top', low):
            found['copper_top'] = found['copper_top'] or str(p)
    return found
