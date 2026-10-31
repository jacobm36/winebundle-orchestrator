#!/bin/sh
# Read-only check of what a Batocera machine offers for running Winebundle.
# Run on the Batocera box (for example over SSH):  sh batocera-probe.sh

section() { printf '\n== %s ==\n' "$1"; }
have() { command -v "$1" >/dev/null 2>&1 && echo "yes ($(command -v "$1"))" || echo "no"; }

section "System"
cat /usr/share/batocera/batocera.version 2>/dev/null || echo "batocera.version not found"
uname -srm
echo "glibc: $(ldd --version 2>/dev/null | head -n1)"
echo "user: $(id -un)"

section "Display"
echo "WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-} DISPLAY=${DISPLAY:-} XDG_SESSION_TYPE=${XDG_SESSION_TYPE:-}"
for p in Xorg Xwayland sway labwc openbox; do printf '%s: ' "$p"; pgrep -x "$p" >/dev/null && echo running || echo -; done

section "Python"
echo "python3: $(have python3)"
python3 --version 2>/dev/null
python3 - <<'PY' 2>/dev/null
for mod in ("gi", "tkinter", "sqlite3", "ssl"):
    try:
        __import__(mod); print(f"{mod}: yes")
    except Exception as e:
        print(f"{mod}: no ({e.__class__.__name__})")
try:
    import gi
    for ns, v in (("Gtk", "3.0"), ("WebKit2", "4.1"), ("WebKit2", "4.0")):
        try:
            gi.require_version(ns, v); print(f"gi {ns} {v}: yes")
        except Exception:
            print(f"gi {ns} {v}: no")
except Exception:
    pass
PY

section "GUI libraries"
for lib in libgtk-3.so.0 libwebkit2gtk-4.1.so.0 libwebkit2gtk-4.0.so.0 libfuse.so.2 libfuse3.so.3; do
  printf '%s: ' "$lib"; ldconfig -p 2>/dev/null | grep -q "$lib" && echo yes || { ls /usr/lib/"$lib" >/dev/null 2>&1 && echo yes || echo no; }
done
echo "fusermount: $(have fusermount)  fusermount3: $(have fusermount3)"
echo "pcmanfm: $(have pcmanfm)  xdg-open: $(have xdg-open)"

section "Wine"
echo "wine on PATH: $(have wine)"
ls -d /usr/wine/* 2>/dev/null || echo "/usr/wine not found"
ls -d /userdata/system/wine/custom/* 2>/dev/null
echo "mksquashfs: $(have mksquashfs)  unsquashfs: $(have unsquashfs)"
echo "vulkaninfo: $(have vulkaninfo)"

section "Storage"
df -h /userdata 2>/dev/null | tail -n1
ls -d /userdata/roms/windows /userdata/roms/windows_installers /userdata/roms/ports 2>/dev/null

section "Ports launchers (sample)"
ls /userdata/roms/ports 2>/dev/null | head -n 10

echo; echo "Done. Please copy everything above and share it."
