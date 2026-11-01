#!/bin/bash
# Batocera Ports launcher for Winebundle. Hotkey + Start closes it (see Winebundle.sh.keys).
exec python3 /userdata/system/winebundle/gui/winebundle_app.py "$@"
