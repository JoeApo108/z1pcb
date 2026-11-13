"""Time loading and a full job (toolpaths, G-code, check) on the IR strip board."""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from z1pcb import cam, gerberio, job  # noqa: E402

D = ROOT / 'tests' / 'data' / 'ir_lista'
t0 = time.time()
design = gerberio.load_design(D / 'ir_lista_cnc-B_Cu.gbl', D / 'ir_lista_cnc-Edge_Cuts.gm1', [D / 'ir_lista_cnc.drl'])
t1 = time.time()
r = job.make(design, cam.default_tools(), cam.Params(), (160, 60))
t2 = time.time()
print('load %.1fs, make + check %.1fs | ok %s, machine time %.1f min' % (t1 - t0, t2 - t1, r.ok, r.prog.total / 60))
