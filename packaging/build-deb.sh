#!/usr/bin/env bash
# Build winebundle_<version>_all.deb from this checkout. Usage: build-deb.sh [output-dir]
set -euo pipefail

ROOT="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
VERSION="${VERSION:-$(tr -d '[:space:]' < "$ROOT/VERSION")}"
OUTPUT="$(mkdir -p "${1:-$ROOT/dist}" && cd "${1:-$ROOT/dist}" && pwd)"
APP_ID="io.github.jacobm36.Winebundle"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

PACKAGE="$STAGE/winebundle"
SHARE="$PACKAGE/usr/share/winebundle"
mkdir -p "$SHARE/worker" "$PACKAGE/DEBIAN" "$PACKAGE/usr/bin" \
  "$PACKAGE/usr/share/applications" "$PACKAGE/usr/share/metainfo" \
  "$PACKAGE/usr/share/icons/hicolor/scalable/apps" "$PACKAGE/usr/share/doc/winebundle"

cp -r "$ROOT/bin" "$ROOT/data" "$ROOT/gui" "$ROOT/lib" "$ROOT/guest" "$SHARE/"
cp -r "$ROOT/worker/winebundle" "$ROOT/worker/lib" "$ROOT/worker/smoke-tests" "$SHARE/worker/"
cp "$ROOT/VERSION" "$SHARE/"
find "$SHARE" \( -name __pycache__ -o -name '*.pyc' \) -prune -exec rm -rf {} +
ln -s ../share/winebundle/bin/winebundle "$PACKAGE/usr/bin/winebundle"

python3 - "$ROOT" "$PACKAGE/usr/share/applications/$APP_ID.desktop" <<'PYTHON'
import sys
sys.path.insert(0, sys.argv[1] + "/gui")
from winebundle_app import desktop_entry
with open(sys.argv[2], "w") as entry:
    entry.write(desktop_entry("/usr/bin/winebundle"))
PYTHON
cp "$ROOT/data/$APP_ID.svg" "$PACKAGE/usr/share/icons/hicolor/scalable/apps/"
cp "$ROOT/data/$APP_ID.metainfo.xml" "$PACKAGE/usr/share/metainfo/"
cp "$ROOT/README.md" "$ROOT/LOCAL-SETUP-TUTORIAL.md" "$PACKAGE/usr/share/doc/winebundle/"
cat > "$PACKAGE/usr/share/doc/winebundle/copyright" <<'COPYRIGHT'
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: Winebundle
Source: https://github.com/jacobm36/winebundle-orchestrator

Files: *
Copyright: 2026 Momentum Studios
License: Expat
COPYRIGHT
sed 's/^$/./; s/^/ /' "$ROOT/LICENSE" | tail -n +3 >> "$PACKAGE/usr/share/doc/winebundle/copyright"

find "$PACKAGE" -type d -exec chmod 755 {} +
find "$PACKAGE" -type f -exec chmod 644 {} +
find "$ROOT/bin" "$ROOT/gui" "$ROOT/guest" "$ROOT/lib" "$ROOT/worker/winebundle" \
  "$ROOT/worker/lib" "$ROOT/worker/smoke-tests" -type f -perm -u+x -printf '%P\t%p\n' |
  while IFS=$'\t' read -r _ source; do
    chmod 755 "$SHARE/${source#"$ROOT/"}"
  done

cat > "$PACKAGE/DEBIAN/control" <<CONTROL
Package: winebundle
Version: $VERSION
Architecture: all
Maintainer: jacobm36 <11908255+jacobm36@users.noreply.github.com>
Installed-Size: $(du -sk --exclude=DEBIAN "$PACKAGE" | cut -f1)
Depends: python3 (>= 3.10), python3-gi, gir1.2-webkit-6.0 | gir1.2-webkit2-4.1
Recommends: wine, winetricks
Section: games
Priority: optional
Homepage: https://github.com/jacobm36/winebundle-orchestrator
Description: install and play Windows games with Wine
 Winebundle guides you through installing a Windows game on Linux, gives
 each game its own Wine environment, tests it before saving it to your
 library, and adds application menu and desktop shortcuts.
 .
 Its System check explains anything else this computer needs, such as
 Wine's 32-bit support or Vulkan drivers.
CONTROL

DEB="$OUTPUT/winebundle_${VERSION}_all.deb"
dpkg-deb --root-owner-group -Zxz --build "$PACKAGE" "$DEB" >/dev/null
echo "$DEB"
