#!/bin/zsh
# Test, build on the Windows build machine, test the installed app there, fetch the installer and sign it.
# Publishing is left as a deliberate manual step (the command is printed at the end).
#   tools/release.sh           release build from a clean, committed tree
#   tools/release.sh --dirty   test build from the working tree as it is
# private/vm.env (not in git) names the build machine: Z1_VM=user@host  Z1_KEY=path-to-ssh-key
set -eo pipefail
cd "$(dirname "$0")/.."
source private/vm.env
ssh_vm=(ssh -o BatchMode=yes -i ${~Z1_KEY} $Z1_VM)
ver=$(sed -n "s/^__version__ = '\(.*\)'/\1/p" z1pcb/__init__.py)
if [ -n "$(git status --porcelain)" ] && [ "$1" != --dirty ]; then
    echo "uncommitted changes: commit first, or use --dirty for a test build" >&2
    exit 1
fi
.venv/bin/python -m pytest -q tests
[ -f wheels/requirements.lock ] || zsh tools/fetch_wheels.sh
tmp=$(mktemp -d)
git ls-files -co --exclude-standard | while read -r f; do if [ -f "$f" ]; then print -r -- "$f"; fi; done | zip -q -@ $tmp/src.zip
zip -qr $tmp/src.zip wheels
scp -q -i ${~Z1_KEY} $tmp/src.zip $Z1_VM:build/z1pcb-src.zip
$ssh_vm 'Remove-Item -Recurse -Force build\z1pcb -ErrorAction SilentlyContinue; Expand-Archive -Force build\z1pcb-src.zip -DestinationPath build\z1pcb; cd build\z1pcb; powershell -NoProfile -ExecutionPolicy Bypass -File tools\build_windows.ps1 -Test; exit $LASTEXITCODE'
mkdir -p dist
setup=dist/Z1PCB-$ver-setup.exe
scp -q -i ${~Z1_KEY} "$Z1_VM:build/z1pcb/$setup" $setup
echo "Signing $setup (Touch ID: the release key is in the guarded csec tier)"
csec get z1pcb-release-signing-key | .venv/bin/python tools/sign_release.py $setup
rm -rf $tmp
echo
echo "To publish:"
echo "  gh release create v$ver $setup $setup.sig --repo JoeApo108/z1pcb-releases --title 'Z1 PCB $ver' --notes '...'"
