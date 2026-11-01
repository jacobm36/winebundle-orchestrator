"""Batocera support: its Wine builds, its Windows game folder and its game list.

Batocera launches Windows games from /userdata/roms/windows/<Name>.wine (a Wine prefix
with an autorun.cmd file) or a compressed <Name>.wsquashfs, using batocera-wine. These
helpers mirror what batocera-wine expects, so games set up here launch from its menu.
"""
import os
import re
import shlex
import shutil
import subprocess
import urllib.request
from pathlib import Path

VERSION_FILE = "usr/share/batocera/batocera.version"
ROMS = Path("/userdata/roms/windows")
PORTS = Path("/userdata/roms/ports")
SYSTEM_WINE = Path("/usr/wine")
CUSTOM_WINE = Path("/userdata/system/wine/custom")
PREFERRED_RUNNER = "wine-proton"
GAME_LIST_RELOAD = "http://127.0.0.1:1234/reloadgames"
# batocera-wine reads autorun.cmd with grep, and a name ends up in batocera.conf keys and
# mount points, so folder names keep to plain characters.
UNSAFE_NAME = re.compile(r"[^\w .,'!&()+-]+")


def active(root=Path("/")):
    return (Path(root) / VERSION_FILE).is_file()


def runners(system=SYSTEM_WINE, custom=CUSTOM_WINE):
    """Batocera's Wine builds that Winebundle can use, preferred first."""
    found = []
    for folder in (system, custom):
        try:
            entries = sorted(folder.iterdir())
        except OSError:
            continue
        for entry in entries:
            # batocera-wine only looks for these two names in /usr/wine.
            if folder == system and entry.name not in ("wine-tkg", "wine-proton"):
                continue
            wine, server = entry / "bin/wine", entry / "bin/wineserver"
            if (wine.is_file() and os.access(wine, os.X_OK) and server.is_file()
                    and os.access(server, os.X_OK)):
                found.append({"name": entry.name, "path": str(wine),
                              "custom": folder == custom})
    found.sort(key=lambda runner: runner["name"] != PREFERRED_RUNNER)
    return found


def runner_name(path, system=SYSTEM_WINE, custom=CUSTOM_WINE):
    """The name Batocera's menu uses for a Wine build, or "" if Batocera can't use it."""
    path = Path(path)
    if path.name != "wine" or path.parent.name != "bin":
        return ""
    base = path.parent.parent
    if base.parent == system and base.name in ("wine-tkg", "wine-proton"):
        return base.name
    return base.name if base.parent == custom else ""


def wine_environment(runner):
    """Library paths batocera-wine sets for its Wine builds, which need them to start."""
    base = Path(runner).parent.parent
    lib64 = base / "lib64/wine" if (base / "lib64/wine").exists() else base / "lib/wine"
    lib32 = base / "lib32/wine" if (base / "lib32/wine").exists() else base / "lib/wine"
    libraries = f"/lib32:{lib32}/i386-unix:/lib:/usr/lib:{lib64}/x86_64-unix"
    if (base / "lib/x86_64-linux-gnu").is_dir():
        libraries += f":{base}/lib/x86_64-linux-gnu:{base}/lib/i386-linux-gnu"
    return {
        "LD_LIBRARY_PATH": libraries,
        "WINEDLLPATH": f"{lib32}/i386-windows:{lib64}/x86_64-windows",
        "GST_PLUGIN_SYSTEM_PATH_1_0": "/usr/lib/gstreamer-1.0:/lib32/gstreamer-1.0",
        "LIBGL_DRIVERS_PATH": "/lib32/dri:/usr/lib/dri",
        "SPA_PLUGIN_DIR": "/usr/lib/spa-0.2:/lib32/spa-0.2",
        "PIPEWIRE_MODULE_DIR": "/usr/lib/pipewire-0.3:/lib32/pipewire-0.3",
    }


def folder_name(name):
    text = UNSAFE_NAME.sub(" ", name)
    text = re.sub(r"\s+", " ", text).strip(" .")
    return text[:80].rstrip(" .") or "Windows game"


def new_prefix(name, roms=ROMS):
    """Reserve a free <Name>.wine folder in Batocera's Windows games folder."""
    base = folder_name(name)
    roms.mkdir(parents=True, exist_ok=True)
    for number in range(1, 1000):
        stem = base if number == 1 else f"{base} ({number})"
        if (roms / f"{stem}.wsquashfs").exists() or (roms / f"{stem}.pc").exists():
            continue
        try:
            (roms / f"{stem}.wine").mkdir(mode=0o755)
        except FileExistsError:
            continue
        return roms / f"{stem}.wine"
    raise OSError("too many games share this name in Batocera's Windows folder")


def managed_path(path, roms=ROMS):
    """Whether a path is a game this module may write to or delete."""
    path = Path(path)
    return (path.parent == roms and path.suffix in (".wine", ".wsquashfs")
            and not path.is_symlink())


def autorun(prefix, entrypoint, args):
    """autorun.cmd for batocera-wine: DIR relative to the prefix, CMD as shell words."""
    game = Path(entrypoint)
    folder = os.path.relpath(game.parent, prefix)
    text = f"DIR={'' if folder == '.' else folder}\n"
    # batocera-wine runs CMD through eval, so it uses shell quoting.
    text += "CMD=" + shlex.join([game.name] + list(args)) + "\n"
    return text


def write_autorun(prefix, entrypoint, args):
    path = Path(prefix) / "autorun.cmd"
    temporary = path.with_name(".autorun.cmd.tmp")
    temporary.write_text(autorun(prefix, entrypoint, args), newline="\n")
    temporary.replace(path)
    return path


def inside(path, folder):
    try:
        Path(os.path.realpath(path)).relative_to(os.path.realpath(folder))
        return True
    except ValueError:
        return False


def set_runner(game_file, runner):
    """Make Batocera launch this game with the Wine build it was set up with."""
    tool = shutil.which("batocera-settings-set")
    if not tool or not runner:
        return False
    try:
        result = subprocess.run([tool, f'windows["{game_file}"].wine-runner', runner],
                                stdin=subprocess.DEVNULL, capture_output=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def reload_games():
    """Ask EmulationStation to rescan its game lists. Best effort."""
    try:
        with urllib.request.urlopen(GAME_LIST_RELOAD, timeout=3) as response:
            return 200 <= response.status < 300
    except (OSError, ValueError):
        return False


def compress_command(prefix, target):
    return ["mksquashfs", str(prefix), str(target), "-comp", "zstd", "-noappend",
            "-no-progress"]
