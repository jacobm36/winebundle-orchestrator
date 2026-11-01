# winebundle-orchestrator

`winebundle-orchestrator` turns a trusted Windows EXE or MSI installer into a
portable, validated Wine prefix. Each build runs in a disposable Ubuntu
Multipass VM, so the host does not need Wine or the application's dependencies
installed.

The orchestrator:

1. creates a fresh VM and copies the installer into it;
2. provisions Wine, Xvfb, winetricks, and supporting tools;
3. runs the installer in a new Wine prefix;
4. removes host-specific links from the prefix;
5. imports the packaged prefix into a separate validation prefix;
6. runs a smoke test; and
7. returns checksummed artifacts and deletes the VM.

This workflow is installer-agnostic. To use it with another Windows
application, provide that installer's unattended arguments, the Windows path
to its installed executable, and an appropriate smoke test.

## Quickstart

### 1. Install the prerequisites

Use a Linux host with:

- [Multipass](https://multipass.run/) installed and able to launch an Ubuntu
  VM;
- Bash 4.4+;
- Python 3.9+;
- GNU `tar`; and
- hardware virtualization enabled.

The default VM needs 2 CPUs, 4 GB of memory, and 20 GB of disk space. Verify
Multipass before starting:

```bash
multipass version
multipass launch 24.04 --name winebundle-check
multipass delete --purge winebundle-check
```

### 2. Build a bundle

Make the orchestrator executable, then replace the sample values below with
values for your installer:

```bash
chmod +x bin/winebundle-vm

bin/winebundle-vm run \
  --installer ./path/to/setup.exe \
  --install-arg /S \
  --entrypoint 'C:\Program Files\Vendor\Application\Application.exe' \
  --smoke-arg --version \
  --output ./artifacts
```

Important:

- `--install-arg` is installer-specific. Common values include `/S`,
  `/VERYSILENT`, or `/quiet`, but you must consult the application's
  documentation. Repeat the option for multiple arguments.
- `--entrypoint` must be the absolute `C:` path at which the installer creates
  the application executable. Use single quotes in Bash so backslashes and
  spaces are preserved.
- The default smoke test runs the entrypoint with any `--smoke-arg` values and
  expects exit code `0`.
- Each install or validation process has a 180-second timeout by default. Use
  `--timeout SECONDS` for a slow installer or application.

For an MSI, pass the MSI directly. The worker invokes it through
`wine msiexec /i`:

```bash
bin/winebundle-vm run \
  --installer ./path/to/application.msi \
  --install-arg /qn \
  --entrypoint 'C:\Program Files\Vendor\Application\Application.exe' \
  --smoke-arg --version \
  --output ./artifacts
```

## Reusable build configurations

Build settings can be stored in a versioned JSON file. Create one from the
same options accepted by `run`:

```bash
bin/winebundle-vm config create ./7zip.json \
  --installer ./7z2604-x64.exe \
  --install-arg /S \
  --entrypoint 'C:\Program Files\7-Zip\7z.exe' \
  --smoke-script ./worker/smoke-tests/7zip-roundtrip.sh \
  --output ./artifacts
```

Validate and run the saved configuration:

```bash
bin/winebundle-vm config validate ./7zip.json
bin/winebundle-vm run --config ./7zip.json
```

Paths in a configuration are resolved relative to the configuration file.
Explicit command-line options override scalar configuration values and append
repeatable values such as `--install-arg`, `--smoke-arg`, and `--winetricks`.
This makes it possible to adjust a known-good configuration without modifying
it:

```bash
bin/winebundle-vm run \
  --config ./7zip.json \
  --timeout 300 \
  --install-arg /NORESTART
```

The version 1 format uses the following fields:

```json
{
  "version": 1,
  "installer": "./setup.exe",
  "entrypoint": "C:\\Program Files\\Vendor\\Application.exe",
  "output": "./artifacts",
  "installArgs": ["/S"],
  "smokeArgs": ["--version"],
  "smokeExitCode": 0,
  "winetricks": ["corefonts"],
  "arch": "win64",
  "timeout": 180,
  "image": "24.04",
  "cpus": 2,
  "memory": "4G",
  "disk": "20G",
  "keepHostDrive": false,
  "skipProvision": false,
  "keepVm": false
}
```

Optional path fields are `smokeScript` and `preInstallScript`; window
validation uses `smokeWindow`. A smoke script cannot be combined with smoke
arguments or window validation.

## Importing a working Batocera Wine game

`bin/winebundle-batocera-import` drafts a configuration from a game that already
runs in Batocera. Copy the game folder from `/userdata/roms/windows/` (and, for
`.pc` games, its prefix from `/userdata/system/wine-bottles/windows/`), or reach
it through the `\\BATOCERA\share` network share, then run:

```bash
bin/winebundle-batocera-import \
  /path/to/userdata/roms/windows/Game.wine ./game.json \
  --installer /absolute/path/to/setup.exe \
  --install-arg /S \
  --smoke-window 'Game Title'
```

It reads `autorun.cmd` (`DIR=` and `CMD=`) for the entrypoint, the prefix's
`system.reg` for the architecture, and `winetricks.log` for winetricks verbs.
Batocera does not keep the installer, so `--installer` and any `--install-arg`
values must be supplied. When the game runs from outside `drive_c` (typical for
`.pc` folders), pass `--entrypoint` with the installed `C:` path. Extract
`.wsquashfs` games with `unsquashfs` first.

Settings the version 1 format cannot represent are printed as notes instead of
being dropped: launch arguments, other `autorun.cmd` entries, `windows.*`
settings from `batocera.conf` (runner, DXVK/VKD3D, sync options), and DLL
overrides from `user.reg`. Load the resulting file with the GUI's import button
or `bin/winebundle-vm run --config ./game.json`.

## Game library and guided local setup

For a worked desktop installation example, see the
[local setup tutorial](LOCAL-SETUP-TUTORIAL.md).

Open the [desktop app](#desktop-app) (or the [web GUI](#local-web-gui)) to
see the home screen. It leads with **Bring your Windows games to Linux**:
choose **A game installer** (setup `.exe`/`.msi`) or **An installed game** (the
game's own `.exe`) to start the guided setup. **Your Windows games**, the library with
**Play** and setup details for each game, is listed below it. `/local` remains
an alias. The app bar has two sections: **Game library** and **System check**.
The VM bundle builder is a developer tool, not part of the app; see
[Local web GUI](#local-web-gui). The VM builder and version 1 bundle
configuration format are unchanged.

### Getting started

The first time Winebundle opens, and whenever the library has no games, it
shows **Getting started**, a three-step guide:

1. **Check this computer** runs the System check below and lists only what
   needs attention, with copyable install commands.
2. **Add your first game** asks whether you have a game installer (setup
   `.exe`/`.msi`) or an already installed game `.exe`, and opens the guided
   setup with that choice made. These buttons stay disabled until anything
   **Required** (such as Wine) is installed.
3. **Test, save and play** explains what happens next.

**Skip to game library** leaves the guide; an empty library links back to it.
Once a game has been added, Winebundle opens to the library instead.

### System check

**System check** (in the header) reports what this computer has for running
Windows games. It opens automatically when Wine is missing and the library
already has games. It checks Wine and its version, Wine's 32-bit support (classic or
WoW64), the GPU driver, 64-bit and 32-bit Vulkan (needed for DXVK), free disk
space, menu/desktop shortcut support, the optional winetricks, and whether
WebKitGTK is available for the app window. Problems
appear as **Required**, **Recommended**, or **Optional**, with install commands
for Debian/Ubuntu, Fedora, Arch, or openSUSE that you can copy and run.

The check is read-only: it never installs packages or asks for your password.
It reads system files and runs only `wine --version`. After installing
something, select **Check again**; a newly installed Wine is then prefilled
when you add a game. A banner in the library summarizes any remaining problems.

1. On the home screen, choose **A game installer** (EXE/MSI) or
   **An installed game** (the game's EXE), then name the game. Step 1 keeps
   that choice; its link (**The game is already installed** /
   **I have an installer instead**) switches it. You can also drag the file
   onto the Winebundle window from your file manager: an installer (a `.msi`,
   or an `.exe` that is named like a setup program or built with Inno Setup,
   NSIS or InstallShield) starts an installer setup, and any other `.exe`
   starts an installed-game setup. Dropping a game `.exe` during **Test**
   selects it as the game to test.
2. Use **Browse...** to choose the source file, or type its absolute Linux
   path. The game name is filled in from the file, for example
   **Blasphemous** from GOG's `setup_blasphemous_4.0.67.1_(64bit)_(85598).exe`,
   or from the game's folder for an installed game; a name you've typed is
   never replaced. Below it, a single line such as **Using Wine 9.0.** shows the Wine
   that will be used: `/usr/bin/wine` (or the `wine` on your `PATH`) when its
   matching `wineserver` is in the same directory. Select **Change** to use
   another runner, such as Wine-GE's `bin/wine`; the field is checked as you
   type, without running Wine. If Wine is missing or invalid, the options open
   with the problem shown. Proton's `proton` wrapper is not supported here.
3. Under **Wine and compatibility options**, choose the architecture.
   Optionally provide an extracted DXVK release
   directory with `x64/` and `x32/` DLLs, and a DXVK GPU name filter.
   The prototype installs only `d3d11.dll` and `dxgi.dll`, for Direct3D 11.
4. Continue to installation, then select **Open the installer** (or **Set up
   Wine for this game** for an installed game). Installers appear on your
   desktop; finish and close them (and any games they automatically open).
   The wizard advances to testing when preparation finishes.
5. Select the installed EXE from **Detected games**, browse the prefix's C:
   drive, or type a `C:\...` or absolute Linux path. Detection searches the prefix's C: drive (including GOG Games, Games,
   and AppData), skip Windows system directories and directory symlinks, and
   rank likely game executables ahead of helpers. A clear match fills an empty
   field without replacing your choice. Paths outside the prefix can be browsed
   or entered as Linux paths. The EXE's parent is used as its working directory.
6. Test the game, play it, and exit normally. On the Finish step, choose
   **Shortcuts** (**Application menu** is selected by default; **Desktop** is
   optional), then answer **Did the game work?** with **It works — save game**,
   or **Not yet — test again** to return to testing.

After saving, the same **Shortcuts** checkboxes (in **Setup & details**, on
the Finish step) add or remove the menu and desktop shortcuts immediately,
without another test. Removing shortcuts does not remove the game.

**Graphics card:** on laptops with two graphics cards (such as Intel with
NVIDIA or AMD), games use the more powerful discrete card by default. This
applies to installs, tests, library Play, and the saved `play.sh` launcher, so
menu and desktop shortcuts use it too. Change the default under **Graphics card
for games** on the System check page, or for one game under **Graphics card**
on its Finish step. Winebundle asks `switcheroo-control` which card to use and
what settings it needs, the same way GNOME's "Launch using Discrete Graphics
Card" does. Without it, Winebundle reads `/sys` and uses NVIDIA's PRIME
render offload or Mesa's `DRI_PRIME`. Saved launchers are updated when
Winebundle starts, so they follow driver changes. The option isn't shown on
computers with one graphics card, or on Batocera, where Batocera's own menu
chooses the card and Winebundle leaves its settings alone. The older **GPU name filter** option is
separate: it only tells DXVK which card to use by name.

**Store web installers:** Winebundle needs a game's offline installer.
If the chosen file is a GOG Galaxy web installer (such as
`GOG_Galaxy_<Game>.exe`) or a store app installer (Steam, Epic Games, EA app,
Ubisoft Connect, Battle.net), step 1 explains why it's unlikely to work and
how to get the offline installer (from GOG, the backup `setup_*.exe` download).
GOG Galaxy installers are recognized by name or by the product name inside the
file. To try the file anyway, tick **Use this file anyway**.

**Removing a game:** in **Setup & details** (or **Continue setup**), select
**Remove game...** and confirm. This deletes the game's setup folder: its Wine
environment (with a game installed into it, and any saves or settings stored
there), launcher, and menu and desktop shortcuts. Any Wine processes still
using it are stopped first. The installer file, or an installed game's own
folder, is kept. It isn't available while that game's installer or test is
running.

The library shows each game's setup status. Search by name, use **Continue
setup** to resume an unfinished installation, or **Play** to launch a saved
game with its confirmed executable and arguments. **Setup & details** opens
the setup steps and activity log; full launcher, prefix, and desktop-entry
paths are under **Technical details**. Running games and
installers can be monitored from the library, with a confirmation before
forcibly stopping them. Only one local operation can run at a time.

Normal Play does not clear gameplay confirmation. To change the executable
or launch arguments, return to the Test step, test the new settings, and save
again. Missing game or runner paths are reported rather than silently changed.

Every setup uses a **new** prefix in
`~/.local/share/winebundle/local/<id>/prefix`, where `<id>` is a readable
name derived from the game name (for example `blasphemous`, or
`blasphemous-2` for a second copy). Setups created by earlier versions keep
their existing hexadecimal folder names. The same folder stores
`setup.json`, `activity.log`, and the saved `play.sh`. Saved setups appear in
the library after restarting the GUI, and confirmed games can be played
without another setup test. After a restart, test again before saving a new
shortcut. Existing saved launchers work without the GUI running.
Menu shortcuts go into `$XDG_DATA_HOME/applications` (normally
`~/.local/share/applications`). Desktop shortcuts go into your desktop folder
(`XDG_DESKTOP_DIR`, normally `~/Desktop`), are marked executable, and are
marked trusted with `gio` when available so GNOME's desktop icons can launch
them. If no desktop folder exists, the Desktop option is unavailable.
The setup records the runner's reported version during preparation, but
references its executable in place; it does not freeze or copy the runner.

This is not a sandbox: installers and games have your user permissions.
Use trusted software only. Existing game folders are used **in place**, not
copied, and the game can modify their files. Back up saves first; existing
prefixes, their registry settings, and their saves are not imported.
Runners and game folders must stay at the recorded paths. No compatibility
success, native conversion, runner bundling, automatic dependency repair,
or gameplay validation is inferred from a successful process exit.

Only one local operation runs at a time in a GUI server. Stop terminates
only that setup's process group and Wine prefix; unsaved progress can be lost.
Keep the app (or GUI server) running until an install or test finishes, and do
not run multiple GUI servers against the same local setups. Use a new setup to try a
different runner or compatibility settings; there is no automatic migration
or retry matrix. The prototype has no winetricks/dependency editor.

## Desktop app

Winebundle runs as a desktop app with its own window.

### Install as a package (Debian and Ubuntu)

Build the `.deb` from the repository root, then install it with `apt`, which
also installs the Python and WebKitGTK packages the window needs, plus Wine
and winetricks:

```bash
make deb
sudo apt install ./dist/winebundle_0.1.0_all.deb
```

The package puts the app in `/usr/share/winebundle`, the `winebundle` command
in `/usr/bin`, and **Winebundle** in the application menu, so
`make install-menu-entry` isn't needed. If you added a menu entry from a
checkout before, run `make remove-menu-entry` so the menu opens the
installed copy. Wine's 32-bit support (`wine32:i386`) needs the i386
architecture enabled, so **System check** gives those commands if needed.
Remove the package with `sudo apt remove winebundle`. Saved games, their
shortcuts and preferences, kept under your home folder, aren't affected by
installing, upgrading or removing it.

The version comes from the `VERSION` file. `packaging/build-deb.sh [folder]`
builds into another folder; it needs only `dpkg-deb` and `python3`.

### Run from a checkout

Start it from the repository root:

```bash
bin/winebundle
```

To add **Winebundle** to the application menu (and dock search), run once:

```bash
make install-menu-entry
```

This writes `io.github.jacobm36.Winebundle.desktop` to
`~/.local/share/applications` and its icon to
`~/.local/share/icons/hicolor/scalable/apps`, pointing at this checkout's
`bin/winebundle`. Run it again if you move the repository, and use
`make remove-menu-entry` to remove it. Saved games and their shortcuts are
not affected.

- **One window:** opening Winebundle again (from the menu or a terminal)
  brings the existing window forward instead of starting a second copy.
- **Closing:** closing the window or pressing `Ctrl+Q` quits immediately when
  nothing is running. Games started with **Play** keep running. If an install
  or test is still running, Winebundle asks first, because closing loses that
  step's result (the installer or game itself keeps running).
- **Without a title bar** (Batocera, or a plain window manager with no desktop
  environment): Winebundle opens maximized and shows a **Quit** button in its
  header. From a terminal, `bin/winebundle --quit` closes the open window, and
  `Ctrl+C` in the terminal that started it works too. These close without
  asking, and games keep running.
- **Requirements:** the window uses WebKitGTK through PyGObject (on
  Debian/Ubuntu, `python3-gi` and `gir1.2-webkit-6.0`; WebKitGTK 4.1 also
  works). Without them, Winebundle opens in your web browser instead, and
  **System check** shows the install command under **App window**. Use
  `bin/winebundle --browser` to choose the browser deliberately.
- **WebKit sandbox:** Ubuntu 24.04 restricts the unprivileged user namespaces
  WebKit's sandbox needs (`kernel.apparmor_restrict_unprivileged_userns=1`)
  unless a `bwrap-userns-restrict` AppArmor profile is installed. On such
  systems only, Winebundle starts WebKit with its sandbox disabled. The window
  loads nothing but Winebundle's own loopback page, under a strict content
  security policy; any other link opens in your web browser.

The app runs the same local server as the web GUI, in-process, on a random
loopback port. Preferences such as the first-run flag and the last folder used
by each **Browse...** field are stored in
`~/.local/share/winebundle/local/preferences.json`, so they survive restarts
and are shared with the web GUI.

## Running on Batocera

Winebundle also runs on [Batocera](https://batocera.org) 43 (x86 PC), using Batocera's
own Python, GTK and WebKit. Install or update it from a checkout on another computer:

```bash
make batocera          # copies to root@batocera.local; password "linux"
```

Then start it from **Ports → Winebundle**. Hotkey + Start closes it, and the D-pad, A
and B move around and select. It detects Batocera by itself and changes how games are
set up:

- Only Batocera's Wine builds are offered (`wine-proton` by default, `wine-tkg`, and
  anything in `/userdata/system/wine/custom/`).
- Each game is installed into `/userdata/roms/windows/<Name>.wine/`. Saving it writes
  `autorun.cmd`, sets its Wine version in Batocera's settings, and refreshes the game
  list, so the game appears in Batocera's **Windows** menu.
- **Compress for Batocera** turns a finished game into a single `.wsquashfs` file.
- Batocera launches the games, so Winebundle's Play button, shortcuts and graphics card
  option are hidden.

This mode hasn't been tried on a Batocera machine yet. See
[docs/BATOCERA-PLAN.md](docs/BATOCERA-PLAN.md) for details and a test checklist.

## Local web GUI

The same interface also runs in a normal web browser. Start the
dependency-free local GUI from the repository root:

```bash
python3 gui/winebundle_gui.py
```

The GUI opens the game library in your browser.

**Browse...** opens an in-app file browser served by the local GUI. Browser
file inputs cannot reveal a file's real path, which the launcher needs, so
the GUI lists folders itself (over its loopback, token-protected API) and
filters them to the relevant file types. It starts from common places such
as Home, Downloads, Games, and mounted drives, and remembers the last folder
used for each field.

This developer server also serves the VM bundle builder at `/builder`
(linked as **VM bundle builder (developer tool)** below the game library;
the desktop app and `bin/winebundle --browser` don't include it). It has
installer, entrypoint, dependency, validation, and VM fields, and can:

- validate and launch a build;
- show build output and completion status;
- export the form as a versioned JSON configuration; and
- import a known-good configuration to reproduce or adjust it.

Use absolute paths in the GUI. Browsers do not expose a selected file's host
path, so installer and script inputs are server-side path fields; configuration
import itself uses a normal browser file picker and is processed locally.

The server listens only on `127.0.0.1` by default, accepts API requests only
from loopback clients addressed as `127.0.0.1`, `localhost`, or `[::1]` (which
blocks DNS-rebinding pages), and requires a per-process session token for
every request except the page assets. The server never gives that token to an
unauthenticated request: the page opens with a one-time launch code after `#`
in its address (never sent to the server as part of the URL), trades it for
the token, and keeps the token for that tab only. A copied link therefore works
once; if a page loses its connection, open Winebundle again. It launches the
orchestrator with an argument array rather than a shell command. Stop it with
`Ctrl+C`. To start without opening a browser, or to select another local port
(open the printed link yourself):

```bash
python3 gui/winebundle_gui.py --no-browser --port 9000
```

## Find and run the result

The command prints the exact result directory. Its layout is:

```text
artifacts/
  <orchestrator-job-id>/
    artifacts/
      <bundle-job-id>/
        prefix.tar.gz
        run.sh
        manifest.json
        SHA256SUMS
        status.json
        build.log
        validation.log
    work/
```

On a Linux machine with Wine and `sha256sum` installed:

```bash
cd artifacts/<orchestrator-job-id>/artifacts/<bundle-job-id>
./run.sh
```

Pass application arguments after `run.sh`. The launcher verifies checksums,
extracts the prefix on first use, and starts the configured entrypoint:

```bash
./run.sh --help
```

By default the prefix is extracted to
`~/.local/share/winebundle/<bundle-job-id>`. Set `WINEBUNDLE_PREFIX_DIR` to
change that location or `WINE` to select a different Wine executable. The
launcher warns when the installed Wine version differs from the version used
to build the bundle. Wine itself is not included.

## Adapting the command to an installer

Before running a new installer, determine:

| Input | What to find |
| --- | --- |
| Installer | A trusted `.exe` or `.msi` file |
| Unattended arguments | Flags that install without user interaction |
| Entrypoint | The final absolute Windows path to the installed executable |
| Architecture | `win64` (default) or `win32` |
| Dependencies | Any required winetricks verbs or pre-install setup |
| Validation | A command, expected exit code, window title, or smoke script |

Start with the smallest working command. Add dependencies and custom validation
only when the application needs them:

```bash
bin/winebundle-vm run \
  --installer ./setup.exe \
  --install-arg /silent \
  --entrypoint 'C:\Program Files\Example\Example.exe' \
  --output ./artifacts
```

With no explicit validation option, the entrypoint is launched with no
arguments and must exit successfully. This is suitable only for applications
that terminate on their own. Use one of the validation strategies below for
other applications.

## Validation strategies

Every build validates the packaged prefix after re-extracting it into a
separate prefix. Choose one strategy.

### Command and exit code

Use repeatable `--smoke-arg` options for a CLI application:

```bash
--smoke-arg --version
```

Exit code `0` is expected by default. For an application whose successful test
uses another code:

```bash
--smoke-arg --self-test \
--smoke-exit-code 7
```

### GUI window

For an application that stays open, wait for a window title instead of waiting
for the process to exit:

```bash
--smoke-window 'Application Name'
```

The test passes when a visible window title contains the supplied text. It
fails if the process exits first or the timeout expires. Do not combine
`--smoke-window` with `--smoke-script` or `--smoke-exit-code`.

### Custom smoke script

Use a trusted Bash script for multi-step or application-specific checks:

```bash
--smoke-script ./smoke-tests/application-smoke.sh
```

The script runs with:

| Variable | Value |
| --- | --- |
| `WINEPREFIX` | Path to the validation prefix |
| `WINE_COMMAND` | Wine executable |
| `ENTRYPOINT` | Configured Windows entrypoint |
| `SMOKE_WORK_DIR` | Writable work directory inside `C:` |

The script must exit nonzero when validation fails. It cannot be combined with
`--smoke-arg` or `--smoke-window`. The 7-Zip round-trip script in
`worker/smoke-tests/7zip-roundtrip.sh` is a complete example.

## Complete 7-Zip example

This example downloads a known installer, installs it silently, creates and
extracts an archive during validation, and publishes the resulting bundle:

```bash
curl -fL \
  https://github.com/ip7z/7zip/releases/download/26.04/7z2604-x64.exe \
  -o 7z2604-x64.exe

chmod +x bin/winebundle-vm
bin/winebundle-vm run \
  --installer ./7z2604-x64.exe \
  --install-arg /S \
  --entrypoint 'C:\Program Files\7-Zip\7z.exe' \
  --smoke-script ./worker/smoke-tests/7zip-roundtrip.sh \
  --output ./artifacts
```

## Installer customization

### winetricks dependencies

Install one or more components before running the installer:

```bash
--winetricks corefonts \
--winetricks vcrun2019
```

Each verb is applied to the build prefix and recorded in `manifest.json`.

### Pre-install hook

Use a trusted Bash script when setup requires more than winetricks:

```bash
--pre-install-script ./scripts/prepare-prefix.sh
```

The hook runs before the Windows installer with these variables set:

- `WINEPREFIX`
- `WINEARCH`
- `WINE_COMMAND`
- `WINEBOOT_COMMAND`
- `WINESERVER_COMMAND`
- `INSTALLER`

The hook and its checksum are included in the published bundle.

### VM resources and prepared images

Override VM sizing when an installer needs more resources:

```bash
--cpus 4 --memory 8G --disk 40G
```

By default the guest installs Wine, Xvfb, `x11-utils`, winetricks, and
`cabextract` on every run. For faster builds, maintain an immutable Ubuntu
image containing those dependencies and use:

```bash
--image <prepared-image> --skip-provision
```

Create a fresh VM from the image for every installer.

## Command reference

Run `bin/winebundle-vm --help` for the authoritative option list.

### Required

| Option | Description |
| --- | --- |
| `--installer PATH` | Trusted EXE or MSI installer |
| `--entrypoint WINDOWS_PATH` | Installed executable at an absolute `C:` path |
| `--output DIR` | Host directory for bundles and diagnostics |

### Installer and validation

| Option | Description |
| --- | --- |
| `--install-arg VALUE` | Installer argument; repeat as needed |
| `--winetricks VERB` | winetricks dependency; repeat as needed |
| `--pre-install-script PATH` | Trusted Bash hook run before installation |
| `--arch win32\|win64` | Prefix architecture; default `win64` |
| `--timeout SECONDS` | Per-process timeout; default `180` |
| `--smoke-arg VALUE` | Entrypoint test argument; repeat as needed |
| `--smoke-exit-code CODE` | Expected test exit code; default `0` |
| `--smoke-window TITLE` | Wait for a window title containing this text |
| `--smoke-script PATH` | Trusted Bash script for custom validation |
| `--keep-host-drive` | Retain Wine's `z:` mapping and host device links |

### VM

| Option | Description |
| --- | --- |
| `--image IMAGE` | Multipass image; default `24.04` |
| `--cpus COUNT` | Virtual CPUs; default `2` |
| `--memory SIZE` | Memory; default `4G` |
| `--disk SIZE` | Disk; default `20G` |
| `--skip-provision` | Assume the image already has all guest dependencies |
| `--keep-vm` | Retain the VM after success or failure for debugging |

Set `MULTIPASS_COMMAND` to use a non-default Multipass executable.
Set `PYTHON_COMMAND` to use a Python 3 executable with a different name or
path.

## Bundle contents

| File | Purpose |
| --- | --- |
| `prefix.tar.gz` | Sanitized installed Wine prefix |
| `run.sh` | Checksum-verifying launcher |
| `manifest.json` | Installer hash, arguments, Wine version, hooks, and test configuration |
| `SHA256SUMS` | Checksums for the published files |
| `status.json` | Final build status |
| `build.log` | Prefix setup and installer output |
| `validation.log` | Smoke-test output |
| `smoke-test.sh` | Included only when a custom smoke script is used |
| `pre-install.sh` | Included only when a pre-install hook is used |

Before packaging, the worker removes the `z:` mapping to the host filesystem
and absolute links in the Wine user profile. The build fails if the
re-extracted bundle still contains host-specific links. Use
`--keep-host-drive` only when the application explicitly requires those
mappings; doing so makes the bundle less portable.

## Failure handling and troubleshooting

The VM is deleted after success, failure, or interruption. On failure, the
orchestrator attempts to retrieve logs and retained Wine working data beneath
the result directory.

- Inspect `build.log` when installation or prefix setup fails.
- Inspect `validation.log` when the entrypoint or smoke test fails.
- Increase `--timeout` if a legitimate process exceeds 180 seconds.
- Confirm the unattended flags if the installer waits for a dialog.
- Confirm `--entrypoint` exactly matches the installed location and
  architecture.
- Add required runtimes with `--winetricks`.
- Use `--keep-vm` only for interactive debugging. The command prints the VM
  name; delete it manually with `multipass delete --purge <name>` afterward.

A timed-out Wine step also runs `wineserver -k` to avoid leaving Wine processes
behind.

## Architecture

```text
bin/winebundle-vm
  -> lib/winebundle_config.py
  -> gui/winebundle_gui.py (optional local interface)

bin/winebundle
  -> gui/winebundle_app.py (desktop window, WebKitGTK)
      -> gui/winebundle_gui.py server (in-process, random loopback port)
          -> lib/local_setup.py, lib/system_check.py
  -> lib/backends/multipass.sh
      -> disposable Ubuntu VM
          -> guest/run-job.sh
              -> Xvfb + worker/winebundle
```

The VM backend is isolated behind shell functions so another backend, such as
libvirt/KVM, can implement the same launch, execute, transfer, and delete
operations.

Do not mount the host workspace into the VM. Inputs and outputs use
`multipass transfer`. The returned archive is treated as untrusted and rejected
if it contains absolute paths, parent-relative paths, hard links, or files
written beneath a symlink created by the archive.

## Security model and limitations

This prototype accepts trusted operator inputs: the installer, pre-install
hook, and smoke script can execute code. Multipass provides a VM boundary, but
its default networking allows outbound access. Do not place host credentials
inside the VM. For untrusted installers, enforce egress filtering outside the
guest or use a backend with an isolated virtual network.

Wine is not a sandbox, and a successful smoke test does not prove that an
application is safe.

Current portability limitations:

- validation uses a second prefix on the same VM, not a different machine or
  user account;
- the prefix still records the build user's name; and
- applications may depend on a specific Wine version even when validation
  succeeds.

## Development

Run the shell test suites from the repository root:

```bash
make test
```

The test target also runs the Python configuration and GUI API tests.

## License

Winebundle is released under the [MIT License](LICENSE).
