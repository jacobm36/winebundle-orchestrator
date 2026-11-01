#!/bin/sh
# Removes Winebundle from Batocera:
#   sh /userdata/system/winebundle/packaging/batocera/uninstall.sh
# Games added with Winebundle stay in /userdata/roms/windows and keep working, since
# Batocera launches them itself. Winebundle's own records of them are kept in
# /userdata/system/.local/share/winebundle, so reinstalling brings them back.
set -eu
python3 /userdata/system/winebundle/gui/winebundle_app.py --quit >/dev/null 2>&1 || true
rm -f /userdata/roms/ports/Winebundle.sh /userdata/roms/ports/Winebundle.sh.keys \
    /userdata/roms/ports/Winebundle.png \
    /userdata/system/.local/share/applications/winebundle.desktop
rm -rf /userdata/system/winebundle
curl -s -o /dev/null --max-time 3 http://127.0.0.1:1234/reloadgames || true
echo "Winebundle was removed. Games you added stay in Batocera's Windows menu."
