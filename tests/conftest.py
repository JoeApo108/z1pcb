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


@pytest.fixture(autouse=True)
def no_makera_studio(monkeypatch, tmp_path):
    """Tests never read the Makera Studio library of the machine they run on."""
    monkeypatch.setenv('Z1PCB_MAKERA_DB', str(tmp_path / 'no-makera-studio.db'))


def fake_makera_db(path):
    """A small library with the shape of Makera Studio's makera_library.db."""
    import sqlite3

    db = sqlite3.connect(path)
    db.executescript(
        """
        create table t_MakeraCutterGroup (cutterGroupid, name, parentId);
        create table t_MaterialList (materialID, materialCategoryID, materialSubcategoryName);
        create table t_MakeraCutterList (cutterId, groupId, cutterName, cutterCategoryId, cutterDiameter,
                                         cutterMaxDiameter, cutterTipDiameter, cutterHalfAngle);
        create table t_CustomCutterList (cutterId, groupId, cutterName, cutterCategoryId, cutterDiameter,
                                         cutterMaxDiameter, cutterTipDiameter, cutterHalfAngle);
        create table t_MakeraCutterProperties (propertiesId, materialId, cutterID, spindleSpeed, feedRate,
                                               plungeFeedRate, stepDown, stepOver, stepOverPercent, coolant);
        create table t_CustomerCutterProperties (propertiesId, materialId, cutterID, spindleSpeed, feedRate,
                                                 plungeFeedRate, stepDown, stepOver, stepOverPercent, coolant);
        create table t_SystemParameters (id, makeraLastUpdate);
        insert into t_SystemParameters values (1, '2026-03-03T07:20:01Z');
        insert into t_MaterialList values ('m-pcb', 'c1', 'PCB'), ('m-cu', 'c2', 'Copper'), ('m-wood', 'c3', 'Hardwood');
        insert into t_MakeraCutterGroup values ('g-flat', 'Flat End', ''), ('g-engr', 'Engraving', ''),
            ('g-flat-metal', 'Single Flute Metal', 'g-flat'), ('g-corn', 'Corn Bits', 'g-flat'),
            ('g-engr-metal', 'Single Flute Metal', 'g-engr'), ('g-mask', 'Solder Mask Remover', 'g-engr');
        insert into t_MakeraCutterList values
            ('v2', 'g-engr-metal', '3.175*0.2mm*30º Engraving(Metal)', 3, 3.175, 3.175, 0.2, 15),
            ('v1', 'g-engr-metal', '3.175*0.1mm*60º Engraving(Metal)', 3, 3.175, 3.175, 0.1, 30),
            ('f2', 'g-flat-metal', '3.175*2*8mm Flat End(Metal)', 1, 3.175, 2.0, 2.0, ''),
            ('f6', 'g-flat-metal', '6*6*20mm Flat End(Metal)', 1, 6.0, 6.0, 6.0, ''),
            ('fw', 'g-flat-metal', '3.175*1.5*6mm Flat End(Wood only)', 1, 3.175, 1.5, 1.5, ''),
            ('c6', 'g-corn', '3.175*0.6*3mm Corn', 1, 3.175, 0.6, 0.6, ''),
            ('c8', 'g-corn', '3.175*0.8*5.5mm Corn', 1, 3.175, 0.8, 0.8, ''),
            ('mk', 'g-mask', '3.175*0.3mm UV Mask Remover', 7, 3.175, 3.175, 0.3, 15);
        insert into t_MakeraCutterProperties values
            (1, 'm-pcb', 'v2', 12000, 500, 200, 0.1, 0.1, 50, 0), (2, 'm-cu', 'v2', 12000, 300, 100, 0.1, 0.1, 50, 0),
            (3, 'm-pcb', 'v1', 12000, 500, 200, 0.1, 0.05, 50, 0),
            (4, 'm-cu', 'f2', 12000, 300, 100, 0.1, 1.26, 63, 0), (5, 'm-cu', 'f6', 12000, 300, 100, 0.1, 3.8, 63, 0),
            (6, 'm-wood', 'fw', 10000, 1000, 300, 0.5, 1.0, 63, 0),
            (7, 'm-pcb', 'c6', 12000, 450, 250, 0.25, 0.38, 63, 0), (8, 'm-pcb', 'c8', 12000, 500, 300, 0.3, 0.5, 63, 0),
            (9, 'm-pcb', 'mk', 6000, 400, 200, 0.2, 0.1, 50, 0);
        insert into t_CustomCutterList values ('{own}', '', 'My 1mm flat', 1, 3.175, 1.0, 1.0, 0);
        insert into t_CustomerCutterProperties values (1, 'm-cu', '{own}', 11000, 250, 80, 0.05, 0.6, 60, 0);
        """
    )
    db.commit()
    db.close()
    return path
