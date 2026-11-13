"""Sign an installer with the release key: writes <installer>.sig next to it.
The private key (hex) comes on stdin and is never an argument:

    csec get z1pcb-release-signing-key | python tools/sign_release.py dist/Z1PCB-x.y.z-setup.exe
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from z1pcb import updates  # noqa: E402

path = sys.argv[1]
key = sys.stdin.read().strip()
if len(key) != 64:
    sys.exit('no private key on stdin (expected 64 hex characters)')
doc = updates.sign(key, path)
raw = json.dumps(doc, indent=1)
updates.verify(raw)  # must check out against the key built into the app, or the release would be refused
Path(path + '.sig').write_text(raw + '\n')
print('%s  version %s  sha256 %s  -> %s.sig' % (doc['name'], doc['version'], doc['sha256'], path))
