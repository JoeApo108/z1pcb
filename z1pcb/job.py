"""The two things the app does, shared by the window and the command line: make checked G-code, check a G-code file."""

import math
from dataclasses import dataclass, field

from . import cam, checks, gcode, gparse
from .errors import UserError
from .fmt import num


@dataclass
class Result:
    design: object
    prog: object  # parsed G-code (moves, timing): also what the simulation plays
    res: dict  # raw check numbers
    geom: dict  # check geometry for the preview
    rows: list  # (label, value, state) for the user
    ok: bool
    expect: object
    frame: object  # design frame <-> machine frame (gcode.Placement or Frame)
    stock: tuple = None  # (x, y) or None when unknown
    text: str = None  # G-code we generated (all programs one after another); None for a file from elsewhere
    parts: list = field(default_factory=list)  # [(file suffix, text)]: one program, or two with a solder mask
    source: str = None  # path of a checked file
    report: dict = field(default_factory=dict)


class Frame:
    """Design <-> machine transform recovered from a file's header or its drill pattern."""

    def __init__(self, mx, tx, ty):
        self.mx, self.tx, self.ty = mx, tx, ty

    def __call__(self, p):
        return (self.mx * p[0] + self.tx, p[1] + self.ty)

    def inverse(self, p):
        return ((p[0] - self.tx) / self.mx, p[1] - self.ty)


def fit_stock(board, tools, prm):
    """Smallest stock in whole millimetres that holds the board and everything the cutters reach."""
    x0, y0, x1, y1 = board.bounds
    m = 2 * cam.reach(tools, prm)
    return (float(math.ceil(x1 - x0 + m)), float(math.ceil(y1 - y0 + m)))


def check_fit(board, stock, tools, prm):
    x0, y0, x1, y1 = board.bounds
    m = cam.reach(tools, prm)
    if x1 - x0 + 2 * m > stock[0] + 1e-6 or y1 - y0 + 2 * m > stock[1] + 1e-6:
        raise UserError(
            'Doska %s × %s mm sa nezmestí do polotovaru %s × %s mm: frézy siahajú %s mm za obrys na každej strane.'
            % (num(x1 - x0, 1), num(y1 - y0, 1), num(stock[0], 1), num(stock[1], 1), num(m, 1))
        )


def make(design, tools, prm, stock, side='bottom', anchor='center', progress=None):
    say = progress or (lambda stage, pct: None)
    check_fit(design.board, stock, tools, prm)
    ops, report = cam.generate(design.copper, design.board, design.holes, tools, prm, progress=say, mask=design.mask)
    say('write', 96)
    place = gcode.Placement(design.board.bounds, stock, side, anchor, cam.reach(tools, prm))
    if prm.mask:  # two programs; the solder mask goes on between them, the board stays on the bed
        parts = [
            (suffix, gcode.render([o for o in ops if o.part == n], place, prm, design.hashes, part=(n, 2)))
            for n, suffix in ((1, '_1_med'), (2, '_2_maska_otvory_obrys'))
        ]
    else:
        parts = [('', gcode.render(ops, place, prm, inputs=design.hashes))]
    text = ''.join(t for _, t in parts)
    prog = gparse.parse_text(text)
    if len(parts) > 1:
        report['part_times'] = [('meď', gparse.parse_text(parts[0][1]).total)]
        report['part_times'].append(('maska, otvory, obrys', prog.total - report['part_times'][0][1]))
    say('check', 97)
    expect = checks.Expect(
        full_clear=prm.full_clear,
        outline_min_pct=report['outline_min_pct'],
        min_hole_d=report['min_hole_d'],
        small_holes=report['small_holes'],
        narrow_cutouts=report['narrow_cutouts'],
        inputs=design.hashes,
        copper_thickness=prm.copper_thickness,
        copper_depth=prm.copper_depth,
        mask=prm.mask,
    )
    res, geom = checks.check(
        prog, design.copper, design.board, design.holes, prm.thickness, expect, want_geom=True, mask=design.mask
    )
    rows, ok = checks.verdict(res, expect)
    return Result(design, prog, res, geom, rows, ok, expect, place, tuple(stock), text=text, parts=parts, report=report)


def check_file(design, path, thickness=1.6, full_clear=True, copper_thickness=0.035, progress=None):
    """Check one G-code file, or several that run one after another (a solder mask job: list of paths)."""
    say = progress or (lambda stage, pct: None)
    say('parse', 10)
    paths = [path] if isinstance(path, (str, bytes)) or not hasattr(path, '__iter__') else list(path)
    texts = []
    for p in paths:
        with open(p, errors='replace') as fh:
            texts.append(fh.read())
    prog = gparse.parse_text('\n'.join(texts))
    say('check', 30)
    with_mask = design.mask is not None and any(t.kind == 'mask' for t in prog.tools.values())
    expect = checks.Expect(
        full_clear=full_clear, inputs=design.hashes, copper_thickness=copper_thickness, mask=with_mask
    )
    res, geom = checks.check(
        prog, design.copper, design.board, design.holes, thickness, expect, want_geom=True, mask=design.mask
    )
    rows, ok = checks.verdict(res, expect)
    stock = prog.stock[:2] if prog.stock else None
    return Result(
        design, prog, res, geom, rows, ok, expect, Frame(res['mx'], res['tx'], res['ty']), stock,
        source=', '.join(map(str, paths)),
    )  # fmt: skip
