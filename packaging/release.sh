#!/bin/sh
# Publish a GitHub release for one product track. Usage: packaging/release.sh ubuntu|batocera
#
#   ubuntu    tag ubuntu-v<VERSION>, with winebundle_<VERSION>_all.deb attached
#   batocera  tag batocera-v<VERSION>, which get-winebundle.sh installs on Batocera
#
# Each track is released on its own, so a fix for one doesn't need a release of the other.
set -eu
cd "$(dirname "$0")/.."

TRACK=${1:-}
case $TRACK in
    ubuntu) NAME="Ubuntu" ;;
    batocera) NAME="Batocera" ;;
    *) echo "Usage: $0 ubuntu|batocera" >&2; exit 2 ;;
esac
VERSION=$(tr -d '[:space:]' < VERSION)
TAG=$TRACK-v$VERSION

[ -z "$(git status --porcelain --untracked-files=no)" ] || {
    echo "Commit your changes first." >&2
    exit 1
}
git fetch -q --tags origin
if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
    echo "$TAG already exists. Change VERSION first." >&2
    exit 1
fi
PREVIOUS=$(git tag -l "$TRACK-v*" --sort=-v:refname | head -n 1)

if [ "$TRACK" = ubuntu ]; then
    packaging/build-deb.sh dist
    ASSET=dist/winebundle_${VERSION}_all.deb
else
    ASSET=packaging/batocera/get-winebundle.sh
fi

git push origin HEAD
set -- --target "$(git rev-parse HEAD)" --title "Winebundle for $NAME $VERSION" \
    --notes-file "packaging/release-notes/$TRACK.md" --generate-notes
if [ -n "$PREVIOUS" ]; then
    set -- "$@" --notes-start-tag "$PREVIOUS"
fi
gh release create "$TAG" "$ASSET" "$@"
