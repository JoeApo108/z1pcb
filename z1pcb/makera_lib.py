"""Read the cutter library of an installed Makera Studio (read-only): tools usable for PCBs and their cutting data.
Makera's data stays on the user's machine; nothing of it is shipped with this app."""

import os
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

FILE = 'makera_library.db'
SHANK = 3.175
# role -> material rows to use, in order of preference (the flat end only cuts copper, so Copper is right for it)
MATERIALS = {'vbit': ('PCB', 'Copper'), 'clear': ('PCB', 'Copper'), 'corn': ('PCB',)}
GROUPS = {  # role -> (parent group, child groups) in Makera's tree
    'vbit': ('Engraving', ('Single Flute Metal', 'Single Flute Non Metal')),
    'clear': ('Flat End', ('Single Flute Metal', 'Single Flute Non Metal')),
    'corn': ('Flat End', ('Corn Bits',)),
}
CUSTOM_ROLES = {1: ('clear', 'corn'), 3: ('vbit',)}  # own tools: category 1 = flat end, 3 = engraving


@dataclass
class LibTool:
    key: str
    name: str
    role: str  # 'vbit', 'clear' or 'corn'
    diameter: float  # flat / corn: cutting diameter; vbit: tip diameter
    half_angle: float
    rpm: int
    feed: int
    plunge: int
    step_down: float
    step_over_pct: float
    material: str  # which material row the values come from
    custom: bool = False

    def label(self):
        name = self.name[len('3.175*') :] if self.name.startswith('3.175*') else self.name
        return name + (' (vlastný)' if self.custom else '')


@dataclass
class Library:
    path: str
    updated: str
    tools: list

    def for_role(self, role):
        return [t for t in self.tools if t.role == role]

    def get(self, key):
        return next((t for t in self.tools if t.key == key), None)


def candidates():
    """Where Makera Studio keeps its library. The Windows locations are a guess (not seen on a real install yet)."""
    if os.environ.get('Z1PCB_MAKERA_DB'):
        return [Path(os.environ['Z1PCB_MAKERA_DB'])]
    home = Path.home()
    if sys.platform == 'darwin':
        return [home / 'Library' / 'Application Support' / 'MakeraStudio' / FILE]
    if sys.platform == 'win32':
        bases = [Path(os.environ[v]) for v in ('APPDATA', 'LOCALAPPDATA') if os.environ.get(v)]
        found = [b / 'MakeraStudio' / FILE for b in bases]
        for b in bases:  # also one level deeper (vendor folder), in case Studio uses one
            found += sorted(b.glob('*/MakeraStudio/' + FILE)) + sorted(b.glob('Makera*/' + FILE))
        return found
    return [home / '.local' / 'share' / 'MakeraStudio' / FILE]


def load(path=None):
    """The library, or None when Makera Studio is not installed or its file cannot be read."""
    for p in [Path(path)] if path else candidates():
        if p.is_file():
            try:
                return read(p)
            except sqlite3.Error:
                return None
    return None


def read(path):
    db = sqlite3.connect('file:%s?mode=ro' % Path(path).as_posix(), uri=True)
    try:
        db.row_factory = sqlite3.Row
        q = lambda sql, *a: [dict(r) for r in db.execute(sql, a)]
        groups = {g['cutterGroupid']: g for g in q('select cutterGroupid, name, parentId from t_MakeraCutterGroup')}
        materials = {m['materialID']: m['materialSubcategoryName'] for m in q('select * from t_MaterialList')}
        props = {}
        for table in ('t_MakeraCutterProperties', 't_CustomerCutterProperties'):
            for p in q('select * from %s' % table):
                props.setdefault(p['cutterID'], {})[materials.get(p['materialId'])] = p
        tools = []
        for c in q('select * from t_MakeraCutterList'):
            g = groups.get(c['groupId']) or {}
            parent = (groups.get(g.get('parentId')) or {}).get('name')
            for role, (top, children) in GROUPS.items():
                if parent == top and g.get('name') in children:
                    tools += make(c, role, props.get(c['cutterId'], {}), custom=False)
        for c in q('select * from t_CustomCutterList'):
            for role in CUSTOM_ROLES.get(c.get('cutterCategoryId'), ()):
                tools += make(c, role, props.get(c['cutterId'], {}), custom=True)
        updated = (q('select makeraLastUpdate from t_SystemParameters') or [{}])[0].get('makeraLastUpdate') or ''
    finally:
        db.close()
    tools.sort(key=lambda t: (t.role, t.custom, t.diameter, t.half_angle, t.name))
    return Library(str(path), updated[:10], tools)


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def make(c, role, props, custom):
    """A LibTool when the cutter fits a 1/8 inch collet and has cutting data for the role's material."""
    if abs(num(c.get('cutterDiameter')) - SHANK) > 0.01:
        return []
    row = next((props[m] for m in MATERIALS[role] if m in props), None)
    if row is None:
        return []
    tip = num(c.get('cutterTipDiameter')) or num(c.get('cutterMaxDiameter'))
    half = num(c.get('cutterHalfAngle'))
    if role == 'vbit' and not (0 < tip <= 0.5 and half > 0):
        return []
    if role != 'vbit' and not 0.3 <= tip <= SHANK:
        return []
    return [
        LibTool(
            key='%s:%s' % (role, c['cutterId']),
            name=c['cutterName'],
            role=role,
            diameter=tip,
            half_angle=half if role == 'vbit' else 0.0,
            rpm=int(num(row.get('spindleSpeed'))),
            feed=int(num(row.get('feedRate'))),
            plunge=int(num(row.get('plungeFeedRate'))),
            step_down=num(row.get('stepDown')),
            step_over_pct=num(row.get('stepOverPercent')),
            material=next(m for m in MATERIALS[role] if m in props),
            custom=custom,
        )
    ]
