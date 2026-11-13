import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
DATA = Path(__file__).parent / 'data' / 'ir_lista'


@pytest.fixture(scope='session')
def ir():
    """IR strip board: KiCad 10, single-sided (bottom), 150 holes, 4 relay cutouts."""
    from z1pcb import gerberio

    return gerberio.load_design(
        DATA / 'ir_lista_cnc-B_Cu.gbl', DATA / 'ir_lista_cnc-Edge_Cuts.gm1', [DATA / 'ir_lista_cnc.drl']
    )


@pytest.fixture(scope='session')
def ir_full(ir):
    """Default job on the IR board (full copper clearing)."""
    from z1pcb import cam, job

    return job.make(ir, cam.default_tools(), cam.Params(), (160, 60))
