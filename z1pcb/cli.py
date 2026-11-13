"""z1pcb make|check: Gerber + Excellon -> Makera Z1 G-code, and verification of a Z1 PCB G-code file."""

import argparse
import sys
from pathlib import Path

from . import cam, gcode, gerberio, job
from .errors import UserError
from .fmt import hms


def stock_arg(s):
    x, y = s.lower().split('x')
    return float(x), float(y)


def main(argv=None):
    ap = argparse.ArgumentParser(prog='z1pcb')
    sub = ap.add_subparsers(dest='cmd', required=True)
    for name in ('make', 'check'):
        p = sub.add_parser(name)
        p.add_argument('--copper', required=True, help='copper Gerber of the milled side')
        p.add_argument('--outline', required=True, help='board outline Gerber (Edge.Cuts)')
        p.add_argument('--drill', nargs='*', default=[], help='Excellon file(s); none for a board without holes')
        p.add_argument('--thickness', type=float, default=1.6)
        p.add_argument('--isolation-only', action='store_true', help='leave free copper, only isolate the nets')
        p.add_argument('--copper-um', type=int, choices=sorted(cam.COPPER_DEPTH), default=35, help='copper foil')
        p.add_argument(
            '--mask', help='solder mask Gerber of the milled side: two programs, pads opened before drilling'
        )
        if name == 'make':
            p.add_argument('--side', choices=('bottom', 'top'), default='bottom')
            p.add_argument('--stock', type=stock_arg, help='e.g. 160x60; default: the smallest that fits')
            p.add_argument('--anchor', choices=gcode.PLACEMENTS, default='center')
            p.add_argument('--depth', type=float, help='depth into the copper side, mm; default from --copper-um')
            p.add_argument('--tabs', type=int, default=4)
            p.add_argument('-o', '--out', required=True)
        else:
            p.add_argument('--gcode', required=True, nargs='+', help='one file, or both programs of a mask job')
    a = ap.parse_args(argv)
    try:
        design = gerberio.load_design(a.copper, a.outline, a.drill, a.mask)
        if a.cmd == 'make':
            tools = cam.default_tools()
            prm = cam.Params(
                thickness=a.thickness,
                copper_thickness=a.copper_um / 1000,
                copper_depth=a.depth or cam.COPPER_DEPTH[a.copper_um],
                tab_count=a.tabs,
                full_clear=not a.isolation_only,
                mask=bool(a.mask),
            )
            stock = a.stock or job.fit_stock(design.board, tools, prm)
            r = job.make(design, tools, prm, stock, a.side, a.anchor)
            out = Path(a.out)
            for suffix, text in r.parts:  # with a mask: <name>_1_med.cnc and <name>_2_maska_otvory_obrys.cnc
                gcode.save(text, out.with_name(out.stem + suffix + (out.suffix or '.cnc')))
            print(
                'stock %g x %g mm, tools T%s, estimated time %s'
                % (*stock, ', T'.join(map(str, r.prog.used_tools())), hms(r.prog.total))
            )
        else:
            r = job.check_file(design, a.gcode, a.thickness, not a.isolation_only, a.copper_um / 1000)
    except UserError as e:
        print('ERROR:', e)
        return 2
    for label, value, state in r.rows:
        print('%-5s %-40s %s' % (state.upper(), label, value))
    print('RESULT:', 'OK' if r.ok else 'PROBLEMS')
    return 0 if r.ok else 1


if __name__ == '__main__':
    sys.exit(main())
