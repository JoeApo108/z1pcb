"""G-code playback for a Makera Z1: material removal on a height map of the stock (top view)."""

import math

import numpy as np

CU = 0.035  # copper thickness
RES = 0.05  # height map cell, mm
SNAP_BUDGET = 256e6  # bytes of height-map snapshots kept for seeking backwards
START_Z = 15.0


def fill_unknown_tools(prog):
    """Give tools without geometry in the header a 0.8 mm flat shape so the file can still be played."""
    from .tool import Tool

    missing = [t for t in prog.used_tools() if t not in prog.tools]
    for t in missing:
        prog.tools[t] = Tool(t, 'T%d (rozmer neznámy, kreslený ako 0,8 mm)' % t, 'flat', 0.8)
    return missing


class Material:
    """Height map over the area the cutters touch: 0 = top of copper, -thickness = bottom of the board."""

    def __init__(self, prog, res=RES):
        self.prog, self.res = prog, res
        self.thick = prog.stock[2] if prog.stock else 1.6
        params = (prog.meta.get('PARAMS') or [{}])[0]  # our files record the copper foil
        self.cu = float(params.get('copper_thickness', CU))
        zmin = np.minimum(prog.p0[:, 2], prog.p1[:, 2]) if len(prog.p0) else np.zeros(0)
        self.cut = zmin < 0  # only moves below the surface remove material
        if self.cut.any():
            reach = 1.0 + max(
                prog.tools[t].radius_at(-float(zmin[self.cut & (prog.tool == t)].min()))
                for t in {int(t) for t in prog.tool[self.cut]}
            )
            pts = np.concatenate([prog.p0[self.cut, :2], prog.p1[self.cut, :2]])
            x0, y0 = pts.min(axis=0) - reach
            x1, y1 = pts.max(axis=0) + reach
        else:
            x0, y0, x1, y1 = 0.0, 0.0, 1.0, 1.0
        self.ox, self.oy = float(x0), float(y0)
        self.nx, self.ny = int((x1 - x0) / res) + 2, int((y1 - y0) / res) + 2
        self.max_snaps = int(max(2, min(12, SNAP_BUDGET // (self.nx * self.ny * 4))))
        self.snaps = {}  # move index -> height map copy
        self.reset()

    def rect(self):
        """Mapped area in machine coordinates: x, y, width, height."""
        return self.ox, self.oy, self.nx * self.res, self.ny * self.res

    def reset(self):
        self.h = np.zeros((self.ny, self.nx), np.float32)
        self.done = 0  # moves fully applied
        self.dirty = (0, 0, self.nx, self.ny)  # cells changed since the last render

    def stamp(self, a, b, tool):
        """Remove material along segment a->b (x, y, z) with the shape of the tool."""
        za, zb = a[2], b[2]
        if min(za, zb) >= 0:
            return
        tl = self.prog.tools[tool]
        reach = tl.radius_at(-min(za, zb)) + self.res
        x0, x1 = min(a[0], b[0]) - reach, max(a[0], b[0]) + reach
        y0, y1 = min(a[1], b[1]) - reach, max(a[1], b[1]) + reach
        i0, i1 = max(0, int((x0 - self.ox) / self.res)), min(self.nx, int((x1 - self.ox) / self.res) + 2)
        j0, j1 = max(0, int((y0 - self.oy) / self.res)), min(self.ny, int((y1 - self.oy) / self.res) + 2)
        if i0 >= i1 or j0 >= j1:
            return
        gx = self.ox + np.arange(i0, i1, dtype=np.float32) * self.res
        gy = self.oy + np.arange(j0, j1, dtype=np.float32) * self.res
        X, Y = np.meshgrid(gx, gy)
        dx, dy = b[0] - a[0], b[1] - a[1]
        l2 = dx * dx + dy * dy
        u = np.clip(((X - a[0]) * dx + (Y - a[1]) * dy) / l2, 0, 1) if l2 > 1e-12 else np.zeros_like(X)
        d = np.hypot(X - (a[0] + u * dx), Y - (a[1] + u * dy))
        zc = za + u * (zb - za)  # tip height along a ramp
        if tl.kind == 'vbit':
            k = 1 / math.tan(math.radians(max(tl.half_angle, 1)))
            hcut = zc + np.maximum(d - tl.diameter / 2, 0) * k
        else:
            hcut = np.where(d <= tl.diameter / 2, zc, np.inf)
        hcut = np.maximum(hcut, -self.thick - 0.5)  # spoil board below
        sub = self.h[j0:j1, i0:i1]
        np.minimum(sub, hcut.astype(np.float32), out=sub)
        d = self.dirty
        self.dirty = (i0, j0, i1, j1) if d is None else (min(d[0], i0), min(d[1], j0), max(d[2], i1), max(d[3], j1))

    def advance_to(self, k):
        """Apply all moves with index < k (uses snapshots when going back)."""
        if k < self.done:
            base = max((s for s in self.snaps if s <= k), default=None)
            if base is None:
                self.reset()
            else:
                self.h = self.snaps[base].copy()
                self.done = base
            self.dirty = (0, 0, self.nx, self.ny)
        p0, p1, tool = self.prog.p0, self.prog.p1, self.prog.tool
        step = max(1, len(p0) // self.max_snaps)
        for i in range(self.done, k):
            if self.cut[i]:
                self.stamp(p0[i], p1[i], int(tool[i]))
            if (i + 1) % step == 0 and (i + 1) not in self.snaps:
                self.snaps[i + 1] = self.h.copy()
        self.done = max(self.done, k)

    def rgb(self):
        """Shaded colour image (ny, nx, 3) uint8, row 0 = top (max Y)."""
        return self.colour(self.h)[::-1].copy()

    def update_image(self, img):
        """Re-colour only the cells changed since the last call into img (row 0 = top). Returns the changed box."""
        if self.dirty is None:
            return None
        i0, j0, i1, j1 = self.dirty
        self.dirty = None
        i0, j0, i1, j1 = max(0, i0 - 2), max(0, j0 - 2), min(self.nx, i1 + 2), min(self.ny, j1 + 2)
        pi0, pj0 = max(0, i0 - 1), max(0, j0 - 1)  # the gradient needs neighbours
        pi1, pj1 = min(self.nx, i1 + 1), min(self.ny, j1 + 1)
        c = self.colour(self.h[pj0:pj1, pi0:pi1])[j0 - pj0 : j1 - pj0, i0 - pi0 : i1 - pi0]
        img[self.ny - j1 : self.ny - j0, i0:i1] = c[::-1]
        return (i0, self.ny - j1, i1, self.ny - j0)

    def colour(self, h):
        gy, gx = np.gradient(h * 25.0)  # exaggerate relief for shading
        shade = np.clip(0.8 + 0.25 * (-gx * 0.6 + gy * 0.8) / np.sqrt(1 + gx * gx + gy * gy), 0.45, 1.15)
        img = np.empty(h.shape + (3,), np.float32)
        depth = np.clip(-h / self.thick, 0, 1)[..., None]
        img[:] = np.array([196, 190, 140], np.float32) * (1 - 0.35 * depth)  # FR4, darker with depth
        img[h > -self.cu + 1e-4] = (205, 140, 70)  # copper
        img[h <= -self.thick + 1e-3] = (40, 44, 50)  # cut through
        img *= shade[..., None]
        return np.clip(img, 0, 255).astype(np.uint8)

    def index_at(self, t):
        return int(np.searchsorted(self.prog.t1, t, side='right'))

    def position(self, t):
        """Tool position and move index at time t."""
        p0, p1 = self.prog.p0, self.prog.p1
        if len(p0) == 0:
            return (0.0, 0.0, START_Z), 0
        k = min(self.index_at(t), len(p0) - 1)
        a, b, ta, tb = p0[k], p1[k], self.prog.t0[k], self.prog.t1[k]
        if t <= ta:
            return tuple(a), k
        u = min(1.0, (t - ta) / max(tb - ta, 1e-9))
        return tuple(a + (b - a) * u), k
