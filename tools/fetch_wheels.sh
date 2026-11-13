#!/bin/zsh
# Download the Windows wheels of the build through safe-chain and lock them by hash.
# Result: wheels/*.whl and wheels/requirements.lock, the only source the Windows build installs from.
cd "$(dirname "$0")/.."
source .venv/bin/activate
[ -f ~/.safe-chain/scripts/init-posix.sh ] && source ~/.safe-chain/scripts/init-posix.sh
if ! type pip | grep -q function; then
    echo "pip is not wrapped by safe-chain here; refusing to download packages unchecked" >&2
    exit 1
fi
# no `set -e`: the safe-chain wrapper itself runs a command that returns non-zero, which would end the script
fail() { echo "fetch_wheels: $1 failed" >&2; exit 1; }
rm -rf wheels.new && mkdir wheels.new || fail mkdir
win=(--dest wheels.new --only-binary=:all: --platform win_amd64 --python-version 3.13 --implementation cp
     --abi cp313 --abi abi3 --abi none)
pip download -q --no-deps -r requirements.txt "${win[@]}" || fail "runtime download"   # exactly the pinned list
pip download -q -r requirements-build.txt -r requirements-dev.txt "${win[@]}" || fail "tools download"  # with deps
rm -f wheels.new/macholib-*  # PyInstaller's macOS-only dependency, pulled in because pip runs on a Mac
python - <<'PY' || fail lock
import hashlib
from pathlib import Path

lines = []
for whl in sorted(Path('wheels.new').glob('*.whl')):
    name, version = whl.name.split('-')[:2]
    lines.append('%s==%s --hash=sha256:%s' % (name, version, hashlib.sha256(whl.read_bytes()).hexdigest()))
Path('wheels.new/requirements.lock').write_text('\n'.join(lines) + '\n')
print('%d wheels locked' % len(lines))
PY
[ -s wheels.new/requirements.lock ] || fail "lock file"
rm -rf wheels && mv wheels.new wheels
