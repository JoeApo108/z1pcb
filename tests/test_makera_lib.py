"""Reading Makera Studio's tool library: which tools are offered for which job, and with which cutting data."""

from conftest import fake_makera_db

from z1pcb import makera_lib


def test_tools_per_role(tmp_path):
    lib = makera_lib.load(fake_makera_db(tmp_path / 'lib.db'))
    names = {role: [t.name for t in lib.for_role(role)] for role in ('vbit', 'clear', 'corn')}
    assert names['vbit'] == ['3.175*0.1mm*60º Engraving(Metal)', '3.175*0.2mm*30º Engraving(Metal)']
    assert names['clear'] == ['3.175*2*8mm Flat End(Metal)', 'My 1mm flat']  # no 6 mm shank, no wood-only data
    assert names['corn'][:2] == ['3.175*0.6*3mm Corn', '3.175*0.8*5.5mm Corn']  # own flat may serve as corn too
    assert lib.updated == '2026-03-03'


def test_material_preference_and_values(tmp_path):
    lib = makera_lib.load(fake_makera_db(tmp_path / 'lib.db'))
    v = lib.get('vbit:v2')
    assert (v.diameter, v.half_angle, v.feed, v.plunge, v.material) == (0.2, 15.0, 500, 200, 'PCB')  # PCB before Copper
    f = lib.get('clear:f2')
    assert (f.diameter, f.feed, f.step_over_pct, f.material) == (2.0, 300, 63, 'Copper')
    c = lib.get('corn:c6')
    assert (c.diameter, c.feed, c.plunge, c.step_down) == (0.6, 450, 250, 0.25)
    own = lib.get('clear:{own}')
    assert own.custom and own.label() == 'My 1mm flat (vlastný)' and own.rpm == 11000


def test_missing_or_broken_library(tmp_path):
    assert makera_lib.load(tmp_path / 'none.db') is None
    broken = tmp_path / 'broken.db'
    broken.write_bytes(b'not a database')
    assert makera_lib.load(broken) is None
