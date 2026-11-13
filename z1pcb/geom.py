"""Small shapely helpers used by loading, CAM and the checks."""

from shapely import MultiPolygon, Polygon, unary_union

QS = 16  # buffer quadrant segments
MIN_STRIP = 0.25  # copper strips narrower than this (mm) tend to peel off and short something


def as_polys(g):
    if g.is_empty:
        return []
    if isinstance(g, Polygon):
        return [g]
    if isinstance(g, MultiPolygon):
        return list(g.geoms)
    return [x for x in getattr(g, 'geoms', []) if isinstance(x, Polygon)]


def strips(g, width=MIN_STRIP, min_len=3 * MIN_STRIP):
    """Parts of g narrower than `width` and at least `min_len` long: what an opening by width/2 removes,
    minus the crumbs it leaves in corners."""
    r = width / 2
    core = g.buffer(-r, quad_segs=8).buffer(r * 1.05, quad_segs=8)
    out = []
    for q in as_polys(g.difference(core)):
        x0, y0, x1, y1 = q.bounds
        if q.area > 0.02 and max(x1 - x0, y1 - y0) >= min_len:
            out.append(q)
    return unary_union(out) if out else Polygon()
