# Winebundle on Batocera

**Status:** Batocera mode is built and tested on the laptop. It is waiting for a first
test on the Batocera machine. See **First test on the machine** below.
**Goal:** Run the Winebundle app itself on Batocera 43 (x86 PC), so games can be added
from the TV. Games it adds should appear in Batocera's own **Windows** menu.

## Decisions so far

- **No fork.** This repo stays the one project. Batocera support is an extra mode.
- **Packaging:** plain files in `/userdata/system/winebundle/`, run by Batocera's own
  Python and started from the **Ports** menu. (An AppImage was planned, but Batocera
  already has GTK and WebKit.) `.wsquashfs` is Batocera's format for *games*, not apps.
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

## Check results (2026-10-31, Batocera 43.1)

Full output: [batocera-probe-output.txt](batocera-probe-output.txt).

- **Python 3.12.8 with GTK 3 and WebKit2 4.1 are installed.** Winebundle's window
  already supports exactly this, so **an AppImage is probably unnecessary**: copy
  Winebundle to `/userdata/system/winebundle/` and start it with the system Python.
- **Display:** Openbox is running (X11). `DISPLAY` was empty only because the check ran
  over SSH; Ports scripts get the real display.
- **Wine:** not on PATH. Builds live in `/usr/wine/wine-tkg`, `/usr/wine/wine-proton`
  and `/userdata/system/wine/custom/wine-11.16`. DXVK is in `/usr/wine/dxvk`.
- **Tools:** `mksquashfs`, `vulkaninfo` and `pcmanfm` are present. `xdg-open` and
  probably `gio` are not.
- **glibc 2.40, FUSE available, 204 GB free on `/userdata`.**
- **Ports launchers** are plain `.sh` files in `/userdata/roms/ports/`.

## Batocera mode (built 2026-10-31, not yet tried on the machine)

Winebundle switches to Batocera mode by itself when
`/usr/share/batocera/batocera.version` exists. No AppImage is needed: Batocera's own
Python, GTK 3 and WebKit2 4.1 run the normal window.

Decisions made with Jake:

- **Wine:** offer only the builds Batocera can launch: `wine-proton` (the default),
  `wine-tkg`, and any folder in `/userdata/system/wine/custom/`. The game is set up with
  the same Wine that Batocera launches it with, so Batocera doesn't upgrade the prefix.
- **Storage:** each game is installed straight into `/userdata/roms/windows/<Name>.wine/`,
  which is its Wine prefix. Names that are taken get ` (2)`, ` (3)`, ….
- **Compress:** a **Compress for Batocera** button on a finished game makes
  `<Name>.wsquashfs` (`mksquashfs … -comp zstd`) and deletes the folder.
- **Ports entry:** Winebundle is started from **Ports → Winebundle**.

What it does:

- **Save** (the "It works" button) writes `autorun.cmd` (`DIR=` relative to the prefix,
  `CMD=` shell-quoted, LF endings). It sets
  `windows["<Name>.wine"].wine-runner` with `batocera-settings-set` and asks
  EmulationStation to reload its game list (`http://127.0.0.1:1234/reloadgames`).
  If the game was tested with a Wine build Batocera can't use, the Finish step says so.
- **Compress** sets the Wine version again for the `.wsquashfs` name and reloads the list.
  Compressed games can't be tested or changed in Winebundle any more.
- **Remove** deletes the game's folder or `.wsquashfs` and reloads the list. A game
  added with "An installed game" keeps its own folder.
- **Hidden on Batocera:** Winebundle's Play button, menu and desktop shortcuts, and the
  graphics card option. The system check skips desktop-only checks and never suggests
  `apt` commands.
- Wine is started with the same library paths that `batocera-wine` sets.
- The file picker offers Windows installers, Windows games, Userdata and USB drives.
- **Controller** ([Winebundle.sh.keys](../packaging/batocera/Winebundle.sh.keys)):
  D-pad up/down moves between controls (Shift+Tab/Tab), left/right are arrow keys, A is
  Enter, B is Esc, X is Space, L1/R1 are Page Up/Down, the left stick moves the mouse,
  R2 clicks, L2 right-clicks, **Hotkey + Start closes Winebundle**.

### Install or update on Batocera

From this checkout on the laptop (the password is `linux`):

```sh
make batocera                    # or: make batocera BATOCERA=root@192.168.x.x
```

This copies `gui`, `lib`, `data`, `VERSION` and `packaging/batocera` to
`/userdata/system/winebundle/`, then puts `Winebundle.sh` and its `.keys` file in
`/userdata/roms/ports/` and reloads the game list. Run it again after any change.

### First test on the machine

1. Open **Ports → Winebundle**. Check that the controller moves between controls and
   that **Hotkey + Start** closes it.
2. Open **System check**: Wine should be `wine-proton`, with no errors.
3. Add a small game you already know works (an installer from
   `/userdata/roms/windows_installers` is ideal). Install, test, then select
   **It works — save game**.
4. Back in Batocera, open the **Windows** menu: the game should be listed. Launch it.
5. Try **Compress for Batocera** on it, then launch the `.wsquashfs` from the menu.
6. If anything fails, open **Activity log and troubleshooting** in Winebundle, or copy
   `/userdata/system/.local/share/winebundle/local/<game>/activity.log`, and share it.
   To start Winebundle from the laptop and see its output instead:
   `ssh -t root@batocera.local DISPLAY=:0 python3 /userdata/system/winebundle/gui/winebundle_app.py`.

Not known until tested: whether Wine starts correctly from Winebundle with the copied
library paths, whether evmapy applies the `.keys` file to the Ports script, and whether
A/B match Jake's controller.

### Still to do

- Typing (game names, file paths) with only a controller: an on-screen keyboard.
- Larger buttons and focus styling for the TV.
- **Export for Batocera** from the desktop app: write `autorun.cmd` and make a
  `.wsquashfs` to copy across. It can reuse `batocera.autorun` and
  `batocera.compress_command`.

### History

- The probe script ([packaging/batocera/batocera-probe.sh](../packaging/batocera/batocera-probe.sh))
  was run on 2026-10-31. An AppImage turned out to be unnecessary.
- The first run on the TV couldn't be closed, because Batocera's Openbox draws no title
  bar. Fixed: on Batocera the window opens maximized with a **Quit** button,
  `winebundle_app.py --quit` closes it, and Ctrl+C over `ssh -t` works (confirmed).

## Wine builds on Batocera (2026-10-31)

All three have `bin/wine` and `bin/wineserver`, so Winebundle's existing Wine check
accepts them without changes:

| Path | Notes |
| --- | --- |
| `/usr/wine/wine-tkg/bin/wine` | Ships with Batocera. Minimal tools (no `wine64`). |
| `/usr/wine/wine-proton/bin/wine` | Ships with Batocera. Has `wine64` and preloaders. |
| `/userdata/system/wine/custom/wine-11.16/bin/wine` | Added by the user; survives updates. |

`batocera-wine` only launches `wine-tkg` and `wine-proton` from `/usr/wine`, plus anything
in `/userdata/system/wine/custom/`, so Batocera mode lists exactly those. It falls back to
`wine-tkg` when a game has no `wine-runner` setting, which is why Winebundle sets one.

## Graphics card: Batocera decides

Batocera's own menu chooses the graphics card, so Winebundle stays out of it there:
no card detection, no graphics option in the app, and inherited graphics settings
(`DRI_PRIME`, NVIDIA offload variables) are passed through unchanged. This is
automatic when `/usr/share/batocera/batocera.version` exists. Games exported for
Batocera must not set these variables in `autorun.cmd` either.

## Relevant code

- [lib/batocera_import.py](../lib/batocera_import.py) — existing Batocera import logic and
  folder knowledge.
- [lib/local_setup.py](../lib/local_setup.py) — game setup, installer detection, EXE choice.
- [lib/batocera.py](../lib/batocera.py) — Batocera mode: Wine builds and their
  environment, folder names, `autorun.cmd`, settings, game list reload, squashfs.
- [lib/system_check.py](../lib/system_check.py) — Wine and system checks, with a Batocera
  branch.
- [gui/winebundle_app.py](../gui/winebundle_app.py) — GTK/WebKit window.
- [packaging/batocera/](../packaging/batocera/) — Ports launcher, controller mapping,
  installer and probe script.
- [tests/test_batocera.py](../tests/test_batocera.py) — Batocera mode tests.
- [gui/winebundle_gui.py](../gui/winebundle_gui.py) — local server and API.

## Prompt to resume with

> We're continuing Batocera support for Winebundle. Read `docs/BATOCERA-PLAN.md`. I ran
> `make batocera` and tried the first test on the machine. Here is what happened: …
