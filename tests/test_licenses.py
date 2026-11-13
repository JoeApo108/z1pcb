"""Every package the app ships has its license text in the folder the installer carries."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_collect_licenses(tmp_path):
    out = tmp_path / 'licenses'
    p = subprocess.run(
        [sys.executable, str(ROOT / 'tools' / 'collect_licenses.py'), str(out)], capture_output=True, text=True
    )
    assert p.returncode == 0, p.stderr
    index = (out / 'THIRD-PARTY.txt').read_text()
    for line in (ROOT / 'requirements.txt').read_text().splitlines():
        name = line.split('#')[0].split('==')[0].strip()
        if name:
            assert any((out / name).iterdir()) and name in index
    assert (out / 'PySide6_Essentials' / 'LGPL-3.0.txt').read_text().startswith('                   GNU LESSER')
    assert (out / 'gerbonara' / 'Apache-2.0.txt').exists() and (out / 'shapely' / 'LICENSE_GEOS').exists()
    assert not list(out.rglob('LicenseRef-Qt-Commercial.txt'))
    assert (out / 'Python-LICENSE.txt').exists() and (out / 'LICENSE.txt').read_text().startswith('MIT License')
