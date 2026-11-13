"""Operations -> Makera Z1 G-code (Smoothieware). T<n> M6 stops for a manual swap and measures the tool (axes homed)."""

import math
from dataclasses import asdict

from . import __version__

PLACEMENTS = ('center', 'corner')
# The Z1 copies each streamed line into a 130-byte buffer without a length check (SerialConsole.cpp, buf[130])
# and keeps 63 characters of it; longer lines depend on the closed ESP32 firmware. Commands stay far below.
MAX_LINE = 128


def packed(prefix, items, limit=MAX_LINE):
    """prefix + '|'-joined items, split over as many lines as needed to stay within the limit."""
    lines, cur = [], []
    for item in items:
        if cur and len(prefix) + len('|'.join(cur + [item])) > limit:
            lines.append(prefix + '|'.join(cur))
            cur = []
        cur.append(item)
    return lines + ([prefix + '|'.join(cur)] if cur else [])


class Placement:
    """Gerber frame -> machine frame. Machine origin: stock front-left corner, Z0 = stock top."""

    def __init__(self, bounds, stock, side='bottom', anchor='center', margin=0.0):
        x0, y0, x1, y1 = bounds
        self.side, self.stock, self.anchor = side, stock, anchor
        self.mx = -1.0 if side == 'bottom' else 1.0  # bottom is milled face-up: mirror X
        if anchor == 'corner':  # board edge `margin` from the stock's front-left corner
            self.tx = margin - min(self.mx * x0, self.mx * x1)
            self.ty = margin - y0
        else:
            self.tx = stock[0] / 2 - self.mx * (x0 + x1) / 2
            self.ty = stock[1] / 2 - (y0 + y1) / 2

    def __call__(self, p):
        return (self.mx * p[0] + self.tx, p[1] + self.ty)

    def inverse(self, p):
        return ((p[0] - self.tx) / self.mx, p[1] - self.ty)


def header(ops, place, prm, inputs, part=None):
    tools = {op.tool.number: op.tool for op in ops}
    out = [
        ';@MKR|BEGIN',
        ';@MKR|MACHINE|id=Z1|name=Makera Z1',
        ';@MKR|STOCK|id=cuboid|length=%g|width=%g|height=%g' % (place.stock[0], place.stock[1], prm.thickness),
        ';@MKR|CAM|id=z1pcb|name=z1pcb|v=%s' % __version__,
        ';@MKR|UNIT|value=mm',
    ]
    out += [tl.header() for _, tl in sorted(tools.items())]
    out.append(';@MKR|END')
    # everything needed to tell later what this file was made from
    out.append(';Z1PCB|GENERATOR|version=%s' % __version__)
    out.append(
        ';Z1PCB|TRANSFORM|mx=%g|tx=%.6f|ty=%.6f|side=%s|anchor=%s'
        % (place.mx, place.tx, place.ty, place.side, place.anchor)
    )
    out += packed(';Z1PCB|PARAMS|', ['%s=%s' % (k, v) for k, v in asdict(prm).items()])
    for n, tl in sorted(tools.items()):
        out.append(';Z1PCB|CUT|number=%d|rpm=%d|feed=%d|plunge=%d' % (n, tl.rpm, tl.feed, tl.plunge))
    for n, tl in sorted(tools.items()):
        if tl.kind == 'mask':
            out.append(';Z1PCB|MASKTOOL|number=%d' % n)
    if part:
        out.append(';Z1PCB|PART|n=%d|of=%d' % part)
    for role, digest in sorted((inputs or {}).items()):
        out.append(';Z1PCB|INPUT|role=%s|sha256=%s' % (role, digest))
    return out


def render(ops, place, prm, inputs=None, part=None):
    """G-code text ('\\n' line ends). `inputs`: role -> sha256 of the design files, recorded in the header;
    `part`: (n, of) when the job is split into programs (solder mask)."""
    L = header(ops, place, prm, inputs, part)
    emit = L.append
    emit('G90 G21')
    st = {'z': prm.clearance_z, 'xy': None}  # 'xy': current position in the design frame (None: unknown)

    def rapid_to(p):
        """Rapid to above p: short hops at the approach height, longer ones at safe Z; then down to the approach."""
        short = st['xy'] is not None and math.dist(st['xy'], p) <= prm.hop_short
        top = prm.approach_z if short else prm.safe_z
        if st['z'] < top:
            emit('G0 Z%.3f' % top)
            st['z'] = top
        emit('G0 X%.4f Y%.4f' % place(p))
        if st['z'] > prm.approach_z:
            emit('G0 Z%.3f' % prm.approach_z)
            st['z'] = prm.approach_z
        st['xy'] = p

    def cut_to(p, f, z=None):
        m = place(p)
        st['xy'] = p
        if z is None:
            emit('G1 X%.4f Y%.4f F%d' % (m[0], m[1], f))
        else:
            emit('G1 X%.4f Y%.4f Z%.3f F%d' % (m[0], m[1], z, f))
            st['z'] = z

    def plunge(z, f):
        emit('G1 Z%.3f F%d' % (z, f))
        st['z'] = z

    loaded = None
    for k, op in enumerate(ops, 1):
        tl = op.tool
        if not op.blocks:
            continue
        emit('')
        emit(';@MKR|TOOLPATH_START|toolpath_number=%d' % k)
        emit(('; %s - T%d %s' % (op.name, tl.number, tl.name))[:MAX_LINE])
        if tl.number != loaded:  # one M6 per tool: every M6 stops the job and re-measures the tool
            emit('T%d M6' % tl.number)
            loaded = tl.number
            st['z'] = prm.clearance_z
            st['xy'] = op.blocks[0].points[0]
            emit('G0 X%.4f Y%.4f' % place(st['xy']))
            emit('S%d M3' % tl.rpm)
            emit('G0 Z%.3f' % prm.clearance_z)
        for b in op.blocks:
            if b.kind == 'path':
                if b.link and st['xy'] is not None and abs(st['z'] - b.z) < 1e-9:
                    cut_to(b.points[0], tl.feed)  # straight across at depth, checked free of copper in cam.link
                else:
                    rapid_to(b.points[0])
                    plunge(b.z, tl.plunge)
                for p in b.points[1:]:
                    cut_to(p, tl.feed)
            elif b.kind == 'peck':
                rapid_to(b.points[0])
                prev = None
                for z in b.levels:
                    if prev is not None:  # back into the hole just above the last depth, at the Z axis limit
                        plunge(prev + 0.2, 600)
                    plunge(z, tl.plunge)
                    emit('G0 Z%.3f' % prm.approach_z)
                    st['z'] = prm.approach_z
                    prev = z
            elif b.kind == 'levels':
                pts = b.points
                s = [0.0]
                for a, c in zip(pts, pts[1:]):
                    s.append(s[-1] + math.dist(a, c))
                rapid_to(pts[0])
                for z in b.levels:
                    if not b.tabs:
                        plunge(z, tl.plunge)
                        for p in pts[1:]:
                            cut_to(p, tl.feed)
                        continue
                    zt = lambda u: max(z, b.tab_top) if any(t0 <= u <= t1 for t0, t1 in b.tabs) else z  # noqa: B023
                    plunge(zt(0.0), tl.plunge)
                    for i, (a, c) in enumerate(zip(pts, pts[1:])):
                        n = max(1, int(math.dist(a, c) / 0.5))
                        for j in range(1, n + 1):
                            u = s[i] + (s[i + 1] - s[i]) * j / n
                            cut_to((a[0] + (c[0] - a[0]) * j / n, a[1] + (c[1] - a[1]) * j / n), tl.feed, zt(u))
        later = [o.tool.number for o in ops[k:] if o.blocks]
        if not later or later[0] != tl.number:
            emit('G0 Z%.3f' % prm.clearance_z)
            emit('M5')
            st['z'] = prm.clearance_z
    L += ['M05', 'G28', 'M02']
    too_long = [ln for ln in L if len(ln) > MAX_LINE]
    if too_long:  # a bug here, not a user error: every line we write is built to fit
        raise ValueError('G-code line longer than %d characters: %s' % (MAX_LINE, too_long[0][:60]))
    return '\n'.join(L) + '\n'


def save(text, path):
    """Write with CRLF line ends, as Makera Studio does."""
    with open(path, 'w', newline='\r\n', encoding='utf-8') as f:
        f.write(text)
