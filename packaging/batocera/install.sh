#!/bin/sh
# Run on Batocera after copying Winebundle to /userdata/system/winebundle.
# Adds Winebundle to the Ports menu. Safe to run again after updating.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
PORTS=/userdata/roms/ports

mkdir -p "$PORTS"
cp "$HERE/Winebundle.sh" "$HERE/Winebundle.sh.keys" "$PORTS/"
chmod 755 "$PORTS/Winebundle.sh"
# Ask EmulationStation to show the new entry; it also appears after a restart.
curl -s -o /dev/null --max-time 3 http://127.0.0.1:1234/reloadgames || true
echo "Winebundle is in the Ports menu. Hotkey + Start closes it."
