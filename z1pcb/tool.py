"""Cutter description shared by CAM, the G-code header, the checks and the simulation."""

import math
from dataclasses import dataclass

SHANK = 3.175


@dataclass
class Tool:
    number: int
    name: str
    kind: str  # 'flat', 'vbit' or 'mask' (spring-loaded scraper: rides on the surface, takes only the mask)
    diameter: float  # flat: cutting diameter; vbit: tip diameter
    half_angle: float = 0.0  # vbit only, degrees
    rpm: int = 12000
    feed: int = 500
    plunge: int = 200

    def radius_at(self, depth):
        """Radius of the cut at the stock surface when the tip is `depth` below it."""
        if self.kind == 'vbit':
            return self.diameter / 2 + max(depth, 0.0) * math.tan(math.radians(self.half_angle))
        return self.diameter / 2  # flat; mask: the spring keeps the tip at the surface

    def header(self, limit=128):
        """Tool line in the header format Makera Studio writes; a long name is cut to keep the line within limit."""
        vbit = self.kind in ('vbit', 'mask')
        line = ';@MKR|TOOL|number=%d|name=%s|type=%s|diameter=%g|tipdiameter=%g|halfAngle=%g'
        values = [self.number, '', 'Engraving' if vbit else 'Flat End', SHANK if vbit else self.diameter]
        values += [self.diameter, self.half_angle]
        room = limit - len(line % tuple(values))
        values[1] = self.name.replace('|', '/')[: max(room, 0)]
        return line % tuple(values)

    @classmethod
    def from_header(cls, line):
        kv = dict(a.split('=', 1) for a in line.split('|')[2:] if '=' in a)
        num = lambda key: float(kv.get(key) or 0)
        vbit = kv.get('type', '').lower().startswith('engrav')
        dia = num('tipdiameter') if vbit else (num('tipdiameter') or num('diameter'))
        kind = 'vbit' if vbit else 'flat'
        if 'solder mask' in kv.get('name', '').lower():  # Makera's spring-loaded mask remover
            kind = 'mask'
        return cls(int(kv['number']), kv.get('name', ''), kind, dia, num('halfAngle'))
