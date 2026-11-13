"""Put the license texts of everything the app ships into one folder, with an index (THIRD-PARTY.txt).
Run in the environment the app is built from:  python tools/collect_licenses.py <out-dir>"""

import os
import re
import shutil
import sys
from importlib.metadata import distribution
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEXTS = ROOT / 'licenses'
EXTRA = {  # texts a wheel does not ship although its license requires them to go with the binary
    'gerbonara': ['Apache-2.0.txt'],
    'PySide6_Essentials': ['LGPL-3.0.txt', 'GPL-3.0.txt'],
    'shiboken6': ['LGPL-3.0.txt', 'GPL-3.0.txt'],
}
SKIP = {'LicenseRef-Qt-Commercial.txt'}  # applies to commercial Qt licensees only, not to this use
NOTES = {
    'PySide6_Essentials': 'Qt libraries used under LGPL-3.0 as separate DLLs (in _internal), which can be replaced. '
    'Qt bundles third-party code: https://doc.qt.io/qt-6/licenses-used-in-qt.html. '
    'Source: https://code.qt.io/cgit/pyside/pyside-setup.git and https://code.qt.io/cgit/qt/',
    'shiboken6': 'Runtime of Qt for Python, LGPL-3.0, same source as PySide6.',
    'shapely': 'Bundles GEOS (LGPL-2.1, source https://github.com/libgeos/geos), see LICENSE_GEOS.',
    'numpy': 'Bundles OpenBLAS and other libraries, see LICENSE.txt.',
    'cryptography': 'Statically links OpenSSL (Apache-2.0).',
}
FIXED = [  # shipped, but not as Python packages
    (
        'PyInstaller bootloader',
        'GPL-2.0-or-later with an exception that allows distribution',
        'https://pyinstaller.org',
    ),
    (
        'Microsoft Visual C++ runtime',
        'redistributable under the Visual Studio license terms',
        'https://visualstudio.microsoft.com',
    ),
]


def runtime():
    for line in (ROOT / 'requirements.txt').read_text().splitlines():
        line = line.split('#')[0].strip()
        if line:
            yield line.split('==')[0]


def python_license():
    for p in (Path(sys.base_prefix) / 'LICENSE.txt', Path(os.__file__).parent / 'LICENSE.txt'):
        if p.is_file():
            return p
    raise SystemExit('the license of this Python build was not found')


def main(out):
    out = Path(out)
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    shutil.copy(ROOT / 'LICENSE', out / 'LICENSE.txt')
    from z1pcb import __version__

    index = ['Z1 PCB %s is MIT licensed (LICENSE.txt). It ships the following software.' % __version__, '']
    missing = []
    for name in runtime():
        dist = distribution(name)
        md = dist.metadata
        lic = md.get('License-Expression') or md.get('License') or '?'
        url = md.get('Home-page') or next(iter(md.get_all('Project-URL') or []), '').split(', ')[-1]
        target = out / name
        copied = []
        for f in dist.files or []:
            parts = f.parts
            info = next((i for i, part in enumerate(parts) if part.endswith('.dist-info')), None)
            if info is None or f.name in SKIP or f.suffix == '.py':
                continue
            if not re.search(r'licen[cs]e|copying|notice', f.name, re.I):
                continue
            rel = Path(*parts[info + 1 :])
            if rel.parts and rel.parts[0] == 'licenses':
                rel = Path(*rel.parts[1:])
            (target / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(dist.locate_file(f), target / rel)
            copied.append(rel.as_posix())
        for text in EXTRA.get(name, []):
            target.mkdir(parents=True, exist_ok=True)
            shutil.copy(TEXTS / text, target / text)
            copied.append(text)
        if not copied:
            missing.append(name)
        index.append('%s %s - %s - %s' % (name, dist.version, lic, url))
        if name in NOTES:
            index.append('  ' + NOTES[name])
        index.append('  files: ' + ', '.join('%s/%s' % (name, c) for c in sorted(copied)))
        index.append('')
    shutil.copy(python_license(), out / 'Python-LICENSE.txt')
    index += [
        'Python %s - PSF-2.0 - https://www.python.org' % sys.version.split()[0],
        '  files: Python-LICENSE.txt',
        '',
    ]
    for name, lic, url in FIXED:
        index += ['%s - %s - %s' % (name, lic, url), '']
    (out / 'THIRD-PARTY.txt').write_text('\n'.join(index), encoding='utf-8')
    if missing:
        raise SystemExit('no license text for: ' + ', '.join(missing))
    print('licenses of %d packages in %s' % (len(list(runtime())), out))


if __name__ == '__main__':
    sys.path.insert(0, str(ROOT))
    main(sys.argv[1])
