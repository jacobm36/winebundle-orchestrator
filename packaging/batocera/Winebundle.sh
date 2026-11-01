#!/bin/bash
# Starts Winebundle on Batocera, from the Ports menu or the F1 Applications menu.
# Hotkey + Start closes it from Ports (see Winebundle.sh.keys). Its output goes to
# /userdata/system/logs/winebundle.log, which helps if the window stays blank.
mkdir -p /userdata/system/logs
exec python3 /userdata/system/winebundle/gui/winebundle_app.py "$@" \
  > /userdata/system/logs/winebundle.log 2>&1
