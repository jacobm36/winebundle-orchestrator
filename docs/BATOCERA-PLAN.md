# Winebundle on Batocera — paused plan

**Status:** On hold (since 2026-10-31) until the Batocera machine is back from repairs.
**Goal:** Run the Winebundle app itself on Batocera 43 (x86 PC), so games can be added
from the TV. Games it adds should appear in Batocera's own **Windows** menu.

## Decisions so far

- **No fork.** This repo stays the one project. Batocera support is an extra mode.
- **Packaging:** an **AppImage** (one compressed file with Python, GTK and WebKit
  bundled), started from Batocera's **Ports** menu. `.wsquashfs` is Batocera's format for
  *games*, not apps, so it is only used for exported games.
- **Rejected for now:** running Winebundle as a network server controlled from a phone
  or laptop. That needs extra security work. It can be revisited later.

## What we know about Batocera 43

- Its system files are read-only and replaced on update. Anything added must live under
  `/userdata`.
- It has no apt and no WebKitGTK. Python 3 is present.
- It has its own Wine builds (not `/usr/bin/wine`).
- Windows games live in `/userdata/roms/windows/<Name>.wine/` (a Wine prefix) or
  `<Name>.pc/` (a plain game folder), with an `autorun.cmd` file using LF line endings:

  ```
  DIR=drive_c/Program Files (x86)/Studio/Game
  CMD="Game.exe" --fullscreen
  SAVEDIR=drive_c/users/root/Documents/Game   # optional, Batocera 42+
  ENV=MESA_EXTENSION_MAX_YEAR=2002             # optional
  LANG=ja_JP.UTF-8                             # optional
  ```

- Either folder can be compressed to `<Name>.wsquashfs` with `mksquashfs`.
- Batocera 43 also includes a "WINE.toolbox" (F1 → file manager) for basic game setup.
- Reference: <https://wiki.batocera.org/systems:windows>

## Step 1 when resuming: run the check script

The script only reads information and changes nothing:
[packaging/batocera/batocera-probe.sh](../packaging/batocera/batocera-probe.sh).

```sh
scp packaging/batocera/batocera-probe.sh root@batocera.local:/userdata/
ssh root@batocera.local sh /userdata/batocera-probe.sh
```

The default login is `root` / `linux`. If `batocera.local` doesn't connect, use the IP
address from **Network Settings**. Save the output (for example as
`docs/batocera-probe-output.txt`) and share it.

Questions the output answers:

| Question | Why it matters |
| --- | --- |
| Wayland or X11? | How the AppImage window opens (GDK backend). |
| Python, GTK, WebKit present? | What the AppImage must bundle (likely all of it). |
| glibc version | The AppImage must be built on an older or equal glibc. |
| FUSE available? | Whether the AppImage runs directly or needs `--appimage-extract-and-run`. |
| Wine locations | Where Winebundle should look instead of `/usr/bin/wine`. |
| `mksquashfs` present? | Whether `.wsquashfs` files can be made on the Batocera machine. |
| `/userdata/roms/windows`, `ports` | Where to write games and the launcher. |

## Steps after that

1. **AppImage build:** add `packaging/build-appimage.sh` and a `make appimage` target.
   Bundle Python, GTK 3 and WebKitGTK 4.1 (expect about 100–150 MB). Test it on this
   laptop first.
2. **Ports launcher:** a small `Winebundle.sh` in `/userdata/roms/ports/` that starts the
   AppImage. Keep the AppImage and app data under `/userdata/system/winebundle/`.
3. **Batocera mode** (detected automatically on Batocera):
   - Use Batocera's Wine instead of `/usr/bin/wine` (paths come from the check output).
   - Install games into `/userdata/roms/windows/<Name>.wine/` and write `autorun.cmd`
     from the chosen EXE (relative `DIR`, quoted `CMD`).
   - Skip the menu and desktop shortcut options, and hide Winebundle's own launch
     button. Batocera's menu launches the games.
   - Optional "Compress for Batocera" action that makes a `.wsquashfs`.
   - Tell the user to refresh the game list (or trigger it if Batocera has an API for it).
4. **Controller and TV use:** larger targets, keyboard and gamepad focus order, and a way
   to type with an on-screen keyboard. This is the most uncertain part and needs
   testing on the TV.
5. **Export from the desktop app (also useful on its own):** an "Export for Batocera"
   button on a finished game. It writes `autorun.cmd` and makes a `.wsquashfs` to copy to
   `/userdata/roms/windows/`. This reuses most of step 3 and can be built without the
   Batocera machine. It is a good first job if work resumes before the machine is back.

## Relevant code

- [lib/batocera_import.py](../lib/batocera_import.py) — existing Batocera import logic and
  folder knowledge.
- [lib/local_setup.py](../lib/local_setup.py) — game setup, installer detection, EXE choice.
- [lib/system_check.py](../lib/system_check.py) — Wine and system checks. Needs a Batocera
  branch.
- [gui/winebundle_app.py](../gui/winebundle_app.py) — GTK/WebKit window (the AppImage
  entry point).
- [gui/winebundle_gui.py](../gui/winebundle_gui.py) — local server and API.
- [packaging/build-deb.sh](../packaging/build-deb.sh) — existing packaging to model the
  AppImage build on.

## Prompt to resume with

> We paused Batocera support for Winebundle. Read `docs/BATOCERA-PLAN.md`. Here is the
> output of `batocera-probe.sh` from my Batocera 43 machine: …
