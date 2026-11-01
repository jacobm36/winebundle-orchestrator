#!/bin/sh
# Run on Batocera after copying Winebundle to /userdata/system/winebundle.
# Adds Winebundle to the Ports menu and to the F1 Applications menu.
# Safe to run again after updating.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
APP=$(cd "$HERE/../.." && pwd)
PORTS=/userdata/roms/ports
# F1 opens a file manager whose Applications menu lists entries in the batocera.linux
# category from here ($HOME/.local/share/applications).
APPS=/userdata/system/.local/share/applications

mkdir -p "$PORTS" "$APPS"
cp "$HERE/Winebundle.sh" "$HERE/Winebundle.sh.keys" "$PORTS/"
# The picture EmulationStation shows for the Ports entry, as for other ports.
cp "$APP/data/io.github.jacobm36.Winebundle.png" "$PORTS/Winebundle.png"
chmod 755 "$PORTS/Winebundle.sh" "$HERE/Winebundle.sh"
cat > "$APPS/winebundle.desktop.tmp" <<ENTRY
[Desktop Entry]
Version=1.0
Type=Application
Name=Winebundle
Comment=Install Windows games for Batocera
Icon=$APP/data/io.github.jacobm36.Winebundle.png
Exec=/bin/bash $HERE/Winebundle.sh
Terminal=false
Categories=Game;batocera.linux;
ENTRY
mv "$APPS/winebundle.desktop.tmp" "$APPS/winebundle.desktop"
# Ask EmulationStation to show the Ports entry; it also appears after a restart.
curl -s -o /dev/null --max-time 3 http://127.0.0.1:1234/reloadgames || true
echo "Winebundle is in the Ports menu and in F1 > Applications."
echo "From Ports, Hotkey + Start closes it."
