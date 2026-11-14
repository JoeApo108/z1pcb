#!/bin/zsh
# Test here, build on a GitHub-hosted Windows machine (.github/workflows/windows.yml: tests, installer, install and
# self-test there), fetch the installer and sign it here. Publishing is a deliberate manual step (printed at the end).
#   tools/release.sh           release build from a clean, committed tree
#   tools/release.sh --dirty   test build from the working tree as it is (pushed as a temporary commit)
set -eo pipefail
cd "$(dirname "$0")/.."
root=$PWD
repo=JoeApo108/z1pcb
tmp=$(mktemp -d)
if [ "$1" = --dirty ]; then  # commit the working tree to a throw-away commit; HEAD and the index stay as they are
    cp .git/index $tmp/index
    GIT_INDEX_FILE=$tmp/index git add -A
    sha=$(git commit-tree $(GIT_INDEX_FILE=$tmp/index git write-tree) -p HEAD -m 'dirty test build')
elif [ -n "$(git status --porcelain)" ]; then
    echo "uncommitted changes: commit first, or use --dirty for a test build" >&2
    exit 1
else
    sha=$(git rev-parse HEAD)
fi
branch=build/${sha:0:12}
echo "Building commit ${sha:0:7}"
# Snapshot the source first: a release is built from the commit, so later edits in the tree cannot leak in
git archive --format=zip -o $tmp/src.zip $sha
mkdir $tmp/src && unzip -q $tmp/src.zip -d $tmp/src
ver=$(sed -n "s/^__version__ = '\(.*\)'/\1/p" $tmp/src/z1pcb/__init__.py)
(cd $tmp/src && "$root/.venv/bin/python" -m pytest -q -n auto -p no:cacheprovider tests)  # the snapshot is what gets tested

git push -q origin "${sha}:refs/heads/${branch}"  # starts the workflow
trap 'git push -q origin --delete $branch 2>/dev/null; rm -rf $tmp' EXIT
for i in {1..30}; do
    run=$(gh run list --repo $repo --branch $branch --json databaseId,headSha,workflowName \
          --jq ".[] | select(.headSha == \"$sha\" and .workflowName == \"windows-build\") | .databaseId" | head -1)
    [ -n "$run" ] && break
    sleep 5
done
[ -n "$run" ] || { echo "the workflow run did not start" >&2; exit 1; }
echo "Windows build: https://github.com/$repo/actions/runs/$run"
gh run watch $run --repo $repo --exit-status --interval 30 > /dev/null || {
    gh run view $run --repo $repo --log-failed | tail -40; exit 1; }
gh run view $run --repo $repo --log | cut -f3- | cut -d' ' -f2- | grep -aE 'passed in|setup\.exe  |^installed self-test|^update check' || true
gh run download $run --repo $repo -n installer -D $tmp/out
mkdir -p dist
setup=dist/Z1PCB-$ver-setup.exe
mv $tmp/out/Z1PCB-$ver-setup.exe $setup
echo "Signing $setup (Touch ID: the release key is in the guarded csec tier)"
csec get z1pcb-release-signing-key | .venv/bin/python tools/sign_release.py $setup
echo
echo "To publish:"
echo "  git push origin main"
echo "  gh release create v$ver $setup $setup.sig --repo JoeApo108/z1pcb-releases --title 'Z1 PCB $ver' --notes '...'"
