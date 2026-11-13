"""One G-code reader for the checks and the simulation: moves with timing on a Makera Z1 (Smoothieware)."""

import math
import re
from dataclasses import dataclass, field

import numpy as np

from .tool import Tool

RAPID_XY, RAPID_Z = 1200.0, 600.0  # mm/min, Z1 firmware config (configZ1.default)
TOOLCHANGE_S = 60.0  # manual swap + automatic tool length measurement (estimate)
SPINDLE_S = 3.0  # spindle spin-up after M3 (estimate)
ARC_TOL = 0.005  # chord error when an arc is cut into straight moves, mm
START_Z = 15.0

WORD = re.compile(r'([A-Z])\s*([-+]?(?:\d+\.?\d*|\.\d+))')


@dataclass
class Program:
    p0: np.ndarray  # (n, 3) move starts, machine frame
    p1: np.ndarray  # (n, 3) move ends
    t0: np.ndarray  # (n,) start time, s
    t1: np.ndarray  # (n,) end time, s
    tool: np.ndarray  # (n,) tool number
    feed: np.ndarray  # (n,) mm/min, 0 = rapid
    line: np.ndarray  # (n,) G-code line number
    events: list  # (time, tool) tool changes
    tools: dict  # number -> Tool, from the header
    stock: tuple  # (x, y, thickness) or None when the file has no stock header
    total: float
    transform: dict = None  # ;Z1PCB|TRANSFORM (design frame -> machine frame), own files only
    meta: dict = field(default_factory=dict)  # other ;Z1PCB|SECTION|k=v lines: section -> list of dicts
    warnings: list = field(default_factory=list)

    def used_tools(self):
        return sorted({int(t) for t in self.tool})

    def undefined_tools(self):
        """Tools that cut but have no geometry in the header (any post processor except Studio and z1pcb)."""
        cutting = self.tool[(self.feed > 0) & (np.minimum(self.p0[:, 2], self.p1[:, 2]) < 0)]
        return sorted({int(t) for t in cutting} - set(self.tools))


def fields(line):
    return dict(a.split('=', 1) for a in line.split('|')[2:] if '=' in a)


def arc_points(x, y, z, w, clockwise, warn, lineno):
    """End points of the chords of a G2/G3 arc in the XY plane (Z changes linearly: helix)."""
    nx, ny, nz = w.get('X', x), w.get('Y', y), w.get('Z', z)
    if 'I' in w or 'J' in w:
        cx, cy = x + w.get('I', 0.0), y + w.get('J', 0.0)
    elif 'R' in w:
        r, d = w['R'], math.hypot(nx - x, ny - y)
        if d < 1e-9 or d > 2 * abs(r) + 1e-6:
            warn.append('riadok %d: oblúk s R sa nedá zostrojiť, nahradený priamkou' % lineno)
            return [(nx, ny, nz)]
        h = math.sqrt(max(r * r - d * d / 4, 0.0))
        side = -1.0 if clockwise == (r < 0) else 1.0  # centre left of the chord for CCW with R > 0
        cx, cy = (x + nx) / 2 + side * h * (ny - y) / d, (y + ny) / 2 - side * h * (nx - x) / d
    else:
        return [(nx, ny, nz)]
    r = math.hypot(x - cx, y - cy)
    a0, a1 = math.atan2(y - cy, x - cx), math.atan2(ny - cy, nx - cx)
    sweep = a1 - a0
    if clockwise and sweep >= 0:
        sweep -= 2 * math.pi
    if not clockwise and sweep <= 0:
        sweep += 2 * math.pi
    step = 2 * math.acos(max(1 - ARC_TOL / r, -1.0)) if r > ARC_TOL else math.pi / 4
    n = max(1, math.ceil(abs(sweep) / step))
    pts = [
        (cx + r * math.cos(a0 + sweep * k / n), cy + r * math.sin(a0 + sweep * k / n), z + (nz - z) * k / n)
        for k in range(1, n)
    ]
    return pts + [(nx, ny, nz)]


def parse_lines(lines):
    tools, stock, transform, meta, warn = {}, None, None, {}, []
    p0, p1, t0, t1, tl, fd, ln, events = [], [], [], [], [], [], [], []
    x = y = 0.0
    z, f, tool, pending, mode, scale, t = START_Z, 300.0, 0, None, 0, 1.0, 0.0

    def move(a, b, rapid, lineno):
        nonlocal t
        dxy, dz = math.hypot(b[0] - a[0], b[1] - a[1]), abs(b[2] - a[2])
        if rapid:
            dt = max(dxy / RAPID_XY, dz / RAPID_Z) * 60
        else:
            dt = max(dxy / min(f, RAPID_XY), dz / min(f, RAPID_Z)) * 60
        if dt > 0:
            p0.append(a)
            p1.append(b)
            t0.append(t)
            t1.append(t + dt)
            tl.append(tool)
            fd.append(0.0 if rapid else f)
            ln.append(lineno)
            t += dt

    for i, raw in enumerate(lines, 1):
        s = raw.strip()
        if s.startswith(';'):
            if s.startswith(';@MKR|TOOL|'):
                tl_ = Tool.from_header(s)
                tools[tl_.number] = tl_
            elif s.startswith(';@MKR|STOCK|'):
                kv = fields(s)
                stock = (float(kv.get('length', 0)), float(kv.get('width', 0)), float(kv.get('height', 1.6)))
            elif s.startswith(';Z1PCB|TRANSFORM|'):
                kv = fields(s)
                transform = {k: (float(v) if k in ('mx', 'tx', 'ty') else v) for k, v in kv.items()}
            elif s.startswith(';Z1PCB|'):
                meta.setdefault(s.split('|')[1], []).append(fields(s))
            continue
        s = re.sub(r'\(.*?\)', '', s.split(';', 1)[0]).strip().upper()
        if not s:
            continue
        words = WORD.findall(s)
        w = {}
        gs, ms = [], []
        for k, v in words:
            if k == 'G':
                gs.append(float(v))
            elif k == 'M':
                ms.append(int(float(v)))
            elif k == 'T':
                pending = int(float(v))
            else:
                w[k] = float(v)
        if 20 in gs:
            scale = 25.4
        if 21 in gs:
            scale = 1.0
        if 91 in gs:
            warn.append('riadok %d: prírastkové súradnice (G91) nie sú podporené' % i)
        if 6 in ms and pending is not None:
            tool = pending
            events.append((t, tool))
            t += TOOLCHANGE_S
        if 3 in ms or 4 in ms:
            t += SPINDLE_S
        if 4 in gs:  # dwell: Smoothieware takes P in ms or S in s
            t += w.get('S', 0.0) + w.get('P', 0.0) / 1000
            continue
        if 28 in gs or 53 in gs:  # machine-frame moves (park position): not part of the cut
            continue
        for g in gs:
            if g in (0, 1, 2, 3):
                mode = int(g)
        if 'F' in w:
            f = max(w['F'] * scale, 1e-6)
        if not any(k in w for k in 'XYZ'):
            continue
        for k in 'XYZIJR':
            if k in w:
                w[k] *= scale
        if mode in (2, 3):
            ends = arc_points(x, y, z, w, mode == 2, warn, i)
        else:
            ends = [(w.get('X', x), w.get('Y', y), w.get('Z', z))]
        for e in ends:
            move((x, y, z), e, mode == 0, i)
            x, y, z = e
    arr = lambda v, dt=float: np.asarray(v, dtype=dt)
    return Program(
        arr(p0).reshape(-1, 3), arr(p1).reshape(-1, 3), arr(t0), arr(t1), arr(tl, int), arr(fd), arr(ln, int),
        events, tools, stock, t, transform, meta, warn,
    )  # fmt: skip


def parse(path):
    with open(path, errors='replace') as fh:
        return parse_lines(fh)


def parse_text(text):
    return parse_lines(text.splitlines())
