#!/bin/sh
# Installs or updates Winebundle on Batocera. Run on the Batocera machine (over SSH or
# from F1 > Applications > xterm):
#
#   curl -fsSL https://github.com/jacobm36/winebundle-orchestrator/releases/latest/download/get-winebundle.sh | sh
#
# It downloads the latest release, puts it in /userdata/system/winebundle and adds
# Winebundle to the Ports menu and F1 > Applications. Games and settings are kept when
# updating. To install a particular release: ... | WINEBUNDLE_VERSION=v0.1.0 sh
set -eu

REPO=jacobm36/winebundle-orchestrator
DEST=/userdata/system/winebundle

fail() {
    echo "Winebundle: $*" >&2
    exit 1
}

[ -f /usr/share/batocera/batocera.version ] || fail "this installer is for Batocera."
for tool in curl tar python3; do
    command -v "$tool" >/dev/null 2>&1 || fail "$tool is missing."
done

TAG=${WINEBUNDLE_VERSION:-}
if [ -z "$TAG" ]; then
    TAG=$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" |
          python3 -c 'import json, sys; print(json.load(sys.stdin)["tag_name"])') ||
        fail "couldn't find the latest release. Is this machine online?"
fi
case $TAG in
    *[!A-Za-z0-9._-]*|"") fail "invalid version: $TAG" ;;
esac

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT INT TERM
echo "Downloading Winebundle $TAG..."
curl -fsSL -o "$WORK/winebundle.tar.gz" \
    "https://github.com/$REPO/archive/refs/tags/$TAG.tar.gz" ||
    fail "download failed."
tar -xzf "$WORK/winebundle.tar.gz" -C "$WORK"
SRC=$(find "$WORK" -mindepth 1 -maxdepth 1 -type d | head -n 1)
[ -n "$SRC" ] && [ -f "$SRC/gui/winebundle_app.py" ] && [ -f "$SRC/packaging/batocera/install.sh" ] ||
    fail "the download doesn't look like Winebundle."

# Build the new copy next to the old one, then swap, so a failed update leaves the
# previous version working.
mkdir -p "$(dirname "$DEST")"
rm -rf "$DEST.new" "$DEST.old"
mkdir -p "$DEST.new/packaging"
cp -R "$SRC/gui" "$SRC/lib" "$SRC/data" "$SRC/VERSION" "$DEST.new/"
cp -R "$SRC/packaging/batocera" "$DEST.new/packaging/"
if [ -d "$DEST" ]; then
    mv "$DEST" "$DEST.old"
fi
mv "$DEST.new" "$DEST"
rm -rf "$DEST.old"

sh "$DEST/packaging/batocera/install.sh"
echo "Installed Winebundle $(cat "$DEST/VERSION" 2>/dev/null || echo "$TAG")."
echo "If Winebundle was open, close it and start it again to use the new version."
