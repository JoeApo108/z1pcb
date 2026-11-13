"""Cutter description shared by CAM, the G-code header, the checks and the simulation."""

import math
from dataclasses import dataclass

SHANK = 3.175


@dataclass
class Tool:
    number: int
    name: str
    kind: str  # 'flat' or 'vbit'
    diameter: float  # flat: cutting diameter; vbit: tip diameter
    half_angle: float = 0.0  # vbit only, degrees
    rpm: int = 12000
    feed: int = 500
    plunge: int = 200

    def radius_at(self, depth):
        """Radius of the cut at the stock surface when the tip is `depth` below it."""
        if self.kind == 'vbit':
            return self.diameter / 2 + max(depth, 0.0) * math.tan(math.radians(self.half_angle))
        return self.diameter / 2

    def header(self):
        """Tool line in the header format Makera Studio writes."""
        vbit = self.kind == 'vbit'
        return ';@MKR|TOOL|number=%d|name=%s|type=%s|diameter=%g|tipdiameter=%g|halfAngle=%g' % (
            self.number,
            self.name.replace('|', '/'),
            'Engraving' if vbit else 'Flat End',
            SHANK if vbit else self.diameter,
            self.diameter,
            self.half_angle,
        )

    @classmethod
    def from_header(cls, line):
        kv = dict(a.split('=', 1) for a in line.split('|')[2:] if '=' in a)
        num = lambda key: float(kv.get(key) or 0)
        vbit = kv.get('type', '').lower().startswith('engrav')
        dia = num('tipdiameter') if vbit else (num('tipdiameter') or num('diameter'))
        return cls(int(kv['number']), kv.get('name', ''), 'vbit' if vbit else 'flat', dia, num('halfAngle'))
