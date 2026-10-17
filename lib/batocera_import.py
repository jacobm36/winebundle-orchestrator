#!/usr/bin/env python3
"""Draft a version 1 build configuration from a working Batocera Wine game.

Batocera records how a game runs, not how it was installed, so the installer
and its arguments must be supplied. Settings that the version 1 format cannot
represent (runner, DXVK/VKD3D, sync options, DLL overrides) are reported so
they are not silently lost.
"""

import argparse
import re
import shlex
import sys
from pathlib import Path, PureWindowsPath

sys.path.insert(0, str(Path(__file__).resolve().parent))

from winebundle_config import VERSION, ConfigError, write_config  # noqa: E402


class BatoceraImportError(ValueError):
    pass


WINETRICKS_VERB = re.compile(r"^[A-Za-z0-9_.+=-]+$")
CONF_LINE = re.compile(r'^windows(?:\["(?P<game>[^"]+)"\])?\.(?P<key>[^=\s]+)=(?P<value>.*)$')
REG_VALUE = re.compile(r'^"(?P<name>[^"]+)"="(?P<value>[^"]*)"$')


def read_text(path):
    data = Path(path).read_bytes()
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    return data.decode("utf-8", errors="replace")


def parse_autorun(path):
    """Return the DIR, CMD and other KEY=VALUE entries of an autorun.cmd."""
    entries = {}
    for line in read_text(path).splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        entries[key.strip().upper()] = value.strip()
    return entries


def split_command(command):
    # Non-POSIX mode keeps Windows backslashes; strip the surrounding quotes.
    try:
        parts = shlex.split(command, posix=False)
    except ValueError:
        parts = command.split()
    parts = [part[1:-1] if len(part) > 1 and part[0] == part[-1] == '"' else part for part in parts]
    if not parts:
        raise BatoceraImportError("autorun.cmd has an empty CMD entry")
    return parts[0], parts[1:]


def entrypoint_from_autorun(directory, executable):
    """Convert DIR/CMD into a C: path when DIR points inside drive_c."""
    relative = PureWindowsPath(directory.replace("/", "\\")) if directory else PureWindowsPath()
    parts = relative.parts
    if not parts or parts[0].lower() != "drive_c":
        return None
    path = PureWindowsPath("C:\\", *parts[1:], *PureWindowsPath(executable.replace("/", "\\")).parts)
    return str(path)


def prefix_arch(prefix):
    system_reg = prefix / "system.reg"
    if system_reg.is_file():
        for line in read_text(system_reg).splitlines()[:10]:
            match = re.match(r"^#arch=(win32|win64)\s*$", line.strip())
            if match:
                return match.group(1)
    windows = prefix / "drive_c" / "windows"
    if windows.is_dir():
        return "win64" if (windows / "syswow64").is_dir() else "win32"
    return None


def winetricks_verbs(prefix):
    log = prefix / "winetricks.log"
    if not log.is_file():
        return []
    verbs = []
    for line in read_text(log).splitlines():
        verb = line.strip()
        if verb and not verb.startswith("#") and WINETRICKS_VERB.match(verb) and verb not in verbs:
            verbs.append(verb)
    return verbs


def dll_overrides(prefix):
    user_reg = prefix / "user.reg"
    if not user_reg.is_file():
        return {}
    overrides = {}
    in_section = False
    for line in read_text(user_reg).splitlines():
        line = line.strip()
        if line.startswith("["):
            in_section = line.lower().startswith("[software\\\\wine\\\\dlloverrides]")
            continue
        match = REG_VALUE.match(line) if in_section else None
        if match:
            overrides[match.group("name")] = match.group("value")
    return overrides


def batocera_settings(conf, game_name):
    """Return system-wide and game-specific windows.* settings."""
    settings = {}
    if not conf or not conf.is_file():
        return settings
    for line in read_text(conf).splitlines():
        match = CONF_LINE.match(line.strip())
        if not match:
            continue
        game = match.group("game")
        if game is None:
            settings.setdefault(match.group("key"), match.group("value"))
        elif game == game_name:
            settings[match.group("key")] = match.group("value")
    return settings


def userdata_root(game_dir):
    # /userdata/roms/windows/<game> -> /userdata
    if game_dir.parent.name == "windows" and game_dir.parent.parent.name == "roms":
        return game_dir.parent.parent.parent
    return None


def locate_prefix(game_dir, explicit):
    if explicit:
        return explicit
    if (game_dir / "drive_c").is_dir():
        return game_dir
    root = userdata_root(game_dir)
    if root:
        bottle = root / "system" / "wine-bottles" / "windows" / f"{game_dir.stem}.wine"
        if bottle.is_dir():
            return bottle
    return None


def build_config(options):
    game_dir = Path(options.game).expanduser().resolve()
    if game_dir.suffix.lower() == ".wsquashfs":
        raise BatoceraImportError(
            "squashfs games must be extracted first, for example: "
            f"unsquashfs -d {game_dir.stem}.wine {game_dir.name}"
        )
    if not game_dir.is_dir():
        raise BatoceraImportError(f"game folder does not exist: {game_dir}")

    notes = []
    prefix = locate_prefix(game_dir, options.prefix and Path(options.prefix).expanduser().resolve())
    if prefix and not prefix.is_dir():
        raise BatoceraImportError(f"prefix folder does not exist: {prefix}")

    autorun = game_dir / "autorun.cmd"
    entries = parse_autorun(autorun) if autorun.is_file() else {}
    executable, launch_args = (None, [])
    if entries.get("CMD"):
        executable, launch_args = split_command(entries["CMD"])

    entrypoint = options.entrypoint
    if not entrypoint and executable:
        entrypoint = entrypoint_from_autorun(entries.get("DIR", ""), executable)
    if not entrypoint:
        if executable:
            raise BatoceraImportError(
                f"autorun.cmd runs {executable!r} from outside drive_c, so its installed "
                "C: path cannot be inferred; pass --entrypoint 'C:\\...\\Game.exe'"
            )
        raise BatoceraImportError("no autorun.cmd CMD entry found; pass --entrypoint 'C:\\...\\Game.exe'")

    config = {
        "version": VERSION,
        "installer": str(Path(options.installer).expanduser().resolve()),
        "entrypoint": entrypoint,
        "output": str(Path(options.output).expanduser().resolve()),
    }
    if options.install_arg:
        config["installArgs"] = options.install_arg

    arch = options.arch or (prefix_arch(prefix) if prefix else None)
    if arch:
        config["arch"] = arch
    else:
        notes.append("architecture not detected; the orchestrator default (win64) applies")

    verbs = winetricks_verbs(prefix) if prefix else []
    if verbs:
        config["winetricks"] = verbs

    if options.smoke_window:
        config["smokeWindow"] = options.smoke_window
    if options.timeout:
        config["timeout"] = options.timeout

    if not prefix:
        notes.append("no Wine prefix found; pass --prefix to import arch and winetricks")
    if launch_args:
        notes.append(
            "launch arguments not imported (the smoke test expects the program to exit): "
            + " ".join(launch_args)
        )
    for key, value in entries.items():
        if key not in {"DIR", "CMD"}:
            notes.append(f"autorun.cmd {key} not represented: {value}")

    conf = Path(options.batocera_conf).expanduser() if options.batocera_conf else None
    if conf is None and userdata_root(game_dir):
        conf = userdata_root(game_dir) / "system" / "batocera.conf"
    for key, value in batocera_settings(conf, game_dir.name).items():
        notes.append(f"Batocera setting not represented: {key}={value}")

    for name, value in (dll_overrides(prefix) if prefix else {}).items():
        notes.append(f"DLL override not represented: {name}={value}")

    return config, notes


def create_parser():
    parser = argparse.ArgumentParser(
        prog="winebundle-batocera-import",
        description="Draft a winebundle configuration from a working Batocera Wine game.",
    )
    parser.add_argument("game", help="Batocera game folder, e.g. /userdata/roms/windows/Game.wine")
    parser.add_argument("config", help="configuration file to write")
    parser.add_argument("--installer", required=True, help="original installer (.exe or .msi)")
    parser.add_argument("--install-arg", action="append", default=[], help="repeatable")
    parser.add_argument("--entrypoint", help="override the C: path of the game executable")
    parser.add_argument("--prefix", help="Wine prefix if it is not found automatically")
    parser.add_argument("--batocera-conf", help="path to batocera.conf")
    parser.add_argument("--arch", choices=("win32", "win64"), help="override detection")
    parser.add_argument("--smoke-window", help="window title that proves the game started")
    parser.add_argument("--timeout", type=int, help="seconds per install or validation step")
    parser.add_argument("--output", default="./artifacts", help="build output folder")
    parser.add_argument("--force", action="store_true", help="overwrite an existing file")
    return parser


def main(argv=None):
    options = create_parser().parse_args(argv)
    destination = Path(options.config)
    if destination.exists() and not options.force:
        print(f"error: {destination} exists; use --force to overwrite", file=sys.stderr)
        return 1
    try:
        config, notes = build_config(options)
        write_config(destination, config)
    except (BatoceraImportError, ConfigError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"wrote {destination}")
    for note in notes:
        print(f"note: {note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
