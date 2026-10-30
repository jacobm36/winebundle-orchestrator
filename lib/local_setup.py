"""Local, interactive Wine setup. Never modifies an existing Wine prefix."""

import getpass
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import threading
import unicodedata
from pathlib import Path

from winebundle_config import ConfigError


# New setups use readable names; older setups use 32-character hex IDs, which also match.
GAME_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
MAX_BROWSE_ENTRIES = 5000
BROWSE_KINDS = {
    "installer": (".exe", ".msi"),
    "exe": (".exe",),
    "runner": None,
    "folder": (),
}


def game_slug(name):
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
    return slug[:40].rstrip("-") or "game"


def check_runner(value):
    """Check a Wine executable and its wineserver without running either."""
    if not isinstance(value, str) or not value or "\0" in value or not Path(value).is_absolute():
        return {"error": "Enter the absolute path to a Wine executable, such as /usr/bin/wine."}
    path = Path(value)
    if path.is_dir():
        return {"error": "This is a folder. Choose the wine executable inside its bin folder."}
    if not path.is_file() or not os.access(path, os.X_OK):
        return {"error": f"No executable Wine program found at {path}."}
    if path.name.lower() == "proton":
        return {"error": "Proton's wrapper isn't supported. Choose Wine or Wine-GE's bin/wine."}
    server = path.parent / "wineserver"
    if not server.is_file() or not os.access(server, os.X_OK):
        return {"error": f"No matching wineserver found beside it in {path.parent}."}
    return {"error": ""}


def default_runner():
    for candidate in ("/usr/bin/wine", shutil.which("wine")):
        if candidate and not check_runner(candidate)["error"]:
            return candidate
    return ""


def applications_folder():
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "applications"


def desktop_folder():
    """Return the user's desktop folder, or None when there isn't one."""
    home = Path.home()
    path = home / "Desktop"
    settings = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config") / "user-dirs.dirs"
    try:
        for line in settings.read_text().splitlines():
            match = re.fullmatch(r'\s*XDG_DESKTOP_DIR\s*=\s*"(.*)"\s*', line)
            if match:
                value = match.group(1)
                if value.startswith("$HOME"):
                    value = str(home) + value[len("$HOME"):]
                path = Path(value)
    except OSError:
        pass
    # user-dirs.dirs points a disabled folder at $HOME itself.
    if not path.is_absolute() or path.resolve() == home.resolve() or not path.is_dir():
        return None
    return path


def places():
    home = Path.home()
    found = [("Home", home)] + [(name, home / name) for name in (
        "Desktop", "Downloads", "Documents", "Games")]
    user = getpass.getuser()
    for media in (Path("/media") / user, Path("/run/media") / user):
        try:
            found += [(drive.name, drive) for drive in sorted(media.iterdir())]
        except OSError:
            pass
    found.append(("Computer", Path("/")))
    return [{"label": label, "path": str(path)} for label, path in found if path.is_dir()]


def browse(path, kind="installer", hidden=False):
    if kind not in BROWSE_KINDS:
        raise ConfigError("unknown file picker type")
    if not isinstance(path, str) or "\0" in path:
        raise ConfigError("folder path must be text")
    requested = Path(path).expanduser() if path else Path.home()
    if not requested.is_absolute():
        raise ConfigError("folder path must be absolute")
    folder = Path(os.path.abspath(requested))
    if not folder.is_dir():
        raise ConfigError(f"folder not found: {folder}")
    extensions = BROWSE_KINDS[kind]
    entries = []
    try:
        with os.scandir(folder) as scan:
            for entry in scan:
                if not hidden and entry.name.startswith("."):
                    continue
                try:
                    is_dir = entry.is_dir()
                    is_file = not is_dir and entry.is_file()
                except OSError:
                    continue
                if is_file:
                    if extensions is None:
                        if not os.access(entry.path, os.X_OK):
                            continue
                    elif Path(entry.name).suffix.lower() not in extensions:
                        continue
                elif not is_dir:
                    continue
                entries.append({"name": entry.name, "path": str(folder / entry.name),
                                "type": "folder" if is_dir else "file"})
    except OSError as exc:
        raise ConfigError(f"cannot open folder {folder}: {exc.strerror or exc}") from exc
    entries.sort(key=lambda item: (item["type"] != "folder", item["name"].casefold(), item["name"]))
    return {"path": str(folder), "parent": str(folder.parent) if folder.parent != folder else "",
            "entries": entries[:MAX_BROWSE_ENTRIES],
            "truncated": len(entries) > MAX_BROWSE_ENTRIES}


class LocalSetup:
    def __init__(self, root=None):
        self.root = Path(root or Path.home() / ".local/share/winebundle/local")
        self.lock = threading.Lock()
        self.active = None
        self.jobs = {}
        self.process = None

    def _directory(self, game_id):
        if not isinstance(game_id, str) or len(game_id) > 64 or not GAME_ID.fullmatch(game_id):
            raise ConfigError("invalid local game ID")
        return self.root / game_id

    def _read(self, game_id):
        try:
            return json.loads((self._directory(game_id) / "setup.json").read_text())
        except (OSError, ValueError) as exc:
            raise ConfigError(f"cannot read local setup: {exc}") from exc

    def _write(self, game_id, config):
        path = self._directory(game_id) / "setup.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(config, indent=2) + "\n")
        temporary.replace(path)

    def list(self):
        if not self.root.exists():
            return []
        with self.lock:
            return [{**self._read(path.name),
                     **self.jobs.get(path.name, {"status": "idle", "error": ""})}
                    for path in sorted(self.root.iterdir())
                    if path.is_dir() and len(path.name) <= 64
                    and GAME_ID.fullmatch(path.name) and (path / "setup.json").is_file()]

    def get(self, game_id):
        with self.lock:
            config = self._read(game_id)
            job = dict(self.jobs.get(game_id, {"status": "idle", "error": ""}))
        directory = self._directory(game_id)
        log = directory / "activity.log"
        with log.open("rb") if log.exists() else open(os.devnull, "rb") as stream:
            stream.seek(0, 2)
            stream.seek(max(0, stream.tell() - 65536))
            job["log"] = stream.read().decode("utf-8", errors="replace")
        candidates, recommended = [], ""
        if job["status"] != "running":
            candidates, recommended = self._executables(directory / "prefix/drive_c", config)
        return {"config": config, **job, "executables": candidates,
                "recommendedExecutable": recommended,
                "shortcuts": self._shortcut_status(config),
                "drive": str(directory / "prefix/drive_c"),
                "directory": str(directory)}

    @staticmethod
    def _executables(drive, config):
        def normalized(value):
            return re.sub(r"[^a-z0-9]", "", value.lower())

        title = normalized(config["name"])
        candidates = {}

        def add(path, relative):
            stem = normalized(path.stem)
            parts = [normalized(part) for part in relative.parts[:-1]]
            helper = bool(re.search(
                r"^(unins|uninstall|setup|install|vcredist|vc_redist|dxsetup|"
                r"unitycrashhandler|crashreport|crashpad|ue4prereq|ueprereq|"
                r"dotnet|easyanticheat|battleye)", path.stem.lower()))
            helper = helper or any(part in {
                "redist", "redistributables", "support", "installer", "commonredist"
            } for part in parts)
            score = 0
            if title and (stem == title or (
                    len(title) >= 4 and stem in {
                        title + suffix for suffix in (
                            "win64shipping", "win32shipping", "x64", "x86")
                    })):
                score += 100
            if title and title in parts:
                score += 30
            if stem in ("game", "startgame") or stem.endswith("shipping"):
                score += 20
            if "launcher" in stem:
                score -= 10
            if helper:
                score -= 1000
            candidates[str(path.absolute())] = score

        def scan_error(exc):
            raise ConfigError(f"cannot scan installed executables: {exc}") from exc

        if drive.is_dir():
            for root, folders, files in os.walk(drive, followlinks=False, onerror=scan_error):
                base = Path(root)
                folders[:] = [
                    folder for folder in folders
                    if not (base / folder).is_symlink()
                    and not (base == drive and folder.lower() == "windows")
                ]
                for filename in files:
                    path = base / filename
                    if path.suffix.lower() == ".exe" and path.is_file():
                        add(path, path.relative_to(drive))
        if config["mode"] == "existing":
            source = Path(config["source"])
            if source.is_file():
                add(source, Path(source.name))
                candidates[str(source.absolute())] = 2000
        ranked = sorted(candidates, key=lambda path: (-candidates[path], path.casefold(), path))
        recommended = ""
        if ranked and candidates[ranked[0]] >= 0:
            if len(ranked) == 1 or candidates[ranked[0]] - candidates[ranked[1]] >= 20:
                recommended = ranked[0]
        return ranked[:300], recommended

    @staticmethod
    def _file(value, label, executable=False):
        if (not isinstance(value, str) or not value or "\0" in value
                or not Path(value).is_absolute()):
            raise ConfigError(f"{label} must be an absolute file path")
        path = Path(value).expanduser()
        if not path.is_file() or (executable and not os.access(path, os.X_OK)):
            raise ConfigError(f"{label} is missing or not executable: {path}")
        return str(path.absolute())

    def create(self, values):
        name = values.get("name")
        if not isinstance(name, str) or not name.strip() or any(c in name for c in "\r\n\0"):
            raise ConfigError("game name is required and must be a single line")
        runner = self._file(values.get("runner"), "Wine runner", executable=True)
        server = self._file(str(Path(runner).parent / "wineserver"), "matching wineserver", True)
        mode = values.get("mode")
        if mode not in ("installer", "existing"):
            raise ConfigError("choose installer or existing game")
        source = self._file(values.get("source"), "installer or game EXE")
        if Path(source).suffix.lower() not in (".exe", ".msi") or (
                mode == "existing" and Path(source).suffix.lower() != ".exe"):
            raise ConfigError("choose an EXE game, or EXE/MSI installer")
        arch = values.get("arch", "win64")
        if arch not in ("win32", "win64"):
            raise ConfigError("architecture must be win32 or win64")
        args = values.get("installArgs", [])
        if not isinstance(args, list) or any(not isinstance(x, str) or "\0" in x for x in args):
            raise ConfigError("installer arguments must be a list of strings")
        gpu = values.get("gpu", "")
        if not isinstance(gpu, str) or any(c in gpu for c in "\0\r\n"):
            raise ConfigError("GPU filter must be a single line")
        dxvk = values.get("dxvk", "")
        if not isinstance(dxvk, str):
            raise ConfigError("DXVK path must be a string")
        if dxvk:
            for platform in (("x64", "x32") if arch == "win64" else ("x32",)):
                for dll in ("d3d11.dll", "dxgi.dll"):
                    self._file(str(Path(dxvk) / platform / dll), "DXVK DLL")
        config = dict(name=name.strip(), runner=runner, wineserver=server, mode=mode,
                      source=source, arch=arch, installArgs=args, gpu=gpu, dxvk=dxvk,
                      entrypoint=source if mode == "existing" else "", args=[],
                      prepared=False, accepted=False)
        with self.lock:
            if self.active:
                raise ConfigError("another local operation is running")
            self.root.mkdir(parents=True, exist_ok=True)
            base = game_slug(config["name"])
            for number in range(1, 1000):
                game_id = base if number == 1 else f"{base}-{number}"
                try:
                    self._directory(game_id).mkdir(mode=0o700)
                    break
                except FileExistsError:
                    continue
            else:
                raise ConfigError("too many setups share this game name")
            config["id"] = game_id
            self._write(game_id, config)
        return game_id

    def _environment(self, game_id, config):
        env = os.environ.copy()
        for key in ("WINEPREFIX", "WINEARCH", "WINEDLLOVERRIDES", "WINELOADER",
                    "WINESERVER", "DXVK_FILTER_DEVICE_NAME"):
            env.pop(key, None)
        env.update(WINEPREFIX=str(self._directory(game_id) / "prefix"),
                   WINEDEBUG="-all", WINESERVER=config["wineserver"])
        if config["gpu"]:
            env["DXVK_FILTER_DEVICE_NAME"] = config["gpu"]
        return env

    def start(self, game_id, action, values):
        if action not in ("prepare", "test", "play"):
            raise ConfigError("unknown local operation")
        with self.lock:
            if self.active:
                raise ConfigError("another local operation is running")
            config = self._read(game_id)
            if action == "prepare" and config["prepared"]:
                raise ConfigError("this setup is already prepared")
            if action == "play":
                if not config["prepared"] or not config["accepted"]:
                    raise ConfigError("test gameplay and save the game before playing")
                self._file(config["entrypoint"], "game EXE")
                self._file(config["runner"], "Wine runner", executable=True)
                self._file(config["wineserver"], "matching wineserver", executable=True)
            if action == "test":
                if not config["prepared"]:
                    raise ConfigError("prepare the prefix first")
                config["entrypoint"] = self._file(values.get("entrypoint"), "game EXE")
                if Path(config["entrypoint"]).suffix.lower() != ".exe":
                    raise ConfigError("game entrypoint must be an EXE")
                args = values.get("args", [])
                if not isinstance(args, list) or any(not isinstance(x, str) or "\0" in x for x in args):
                    raise ConfigError("game arguments must be a list of strings")
                config.update(args=args, accepted=False)
                self._write(game_id, config)
            self.active = game_id
            self.jobs[game_id] = {"status": "running", "action": action, "error": ""}
            threading.Thread(target=self._run, args=(game_id, action, config), daemon=True).start()

    def stop(self, game_id):
        with self.lock:
            if self.active != game_id:
                raise ConfigError("this setup has no running operation")
            config = self._read(game_id)
            self.jobs[game_id]["cancelled"] = True
            if self.process and self.process.poll() is None:
                os.killpg(self.process.pid, signal.SIGTERM)
            subprocess.run([config["wineserver"], "-k"],
                           env=self._environment(game_id, config),
                           check=True, timeout=15, capture_output=True)

    def _run(self, game_id, action, config):
        directory = self._directory(game_id)
        env = self._environment(game_id, config)
        error = ""
        try:
            with (directory / "activity.log").open("a", buffering=1) as log:
                def run(arguments, cwd, extra=None, timeout=None):
                    with self.lock:
                        if self.jobs[game_id].get("cancelled"):
                            raise ConfigError("operation stopped by user")
                        log.write(f"\n$ {shlex.join(arguments)}\n")
                        process = subprocess.Popen(
                            arguments, cwd=cwd, env={**env, **(extra or {})},
                            stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                        self.process = process
                    try:
                        code = process.wait(timeout=timeout)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait()
                        raise
                    if code:
                        raise subprocess.CalledProcessError(code, arguments)

                if action == "prepare":
                    version = subprocess.run(
                        [config["runner"], "--version"], env=env, check=True,
                        capture_output=True, text=True, timeout=15)
                    config["runnerVersion"] = version.stdout.strip()
                    log.write(f"Selected runner: {config['runnerVersion']}\n")
                    if not config["prepared"]:
                        # wineboot must finish before installer processes start.
                        run([config["runner"], "wineboot", "-i"], directory,
                            {"WINEARCH": config["arch"], "WINEDLLOVERRIDES": "mscoree,mshtml="},
                            timeout=180)
                        run([config["wineserver"], "-w"], directory, timeout=180)
                    if config["dxvk"]:
                        platforms = [("x32", "system32")] if config["arch"] == "win32" else [
                            ("x64", "system32"), ("x32", "syswow64")]
                        for platform, system in platforms:
                            for dll in ("d3d11", "dxgi"):
                                target = directory / "prefix/drive_c/windows" / system / f"{dll}.dll"
                                target.unlink(missing_ok=True)
                                shutil.copyfile(Path(config["dxvk"]) / platform / f"{dll}.dll",
                                                target)
                        for dll in ("d3d11", "dxgi"):
                            run([config["runner"], "reg", "add",
                                 r"HKCU\Software\Wine\DllOverrides", "/v", dll,
                                 "/d", "native,builtin", "/f"], directory, timeout=30)
                    if config["mode"] == "installer":
                        command = [config["runner"]]
                        if Path(config["source"]).suffix.lower() == ".msi":
                            command += ["msiexec", "/i"]
                        run(command + [config["source"]] + config["installArgs"],
                            Path(config["source"]).parent)
                        run([config["wineserver"], "-w"], directory)
                    with self.lock:
                        if self.jobs[game_id].get("cancelled"):
                            raise ConfigError("operation stopped by user")
                        config["prepared"] = True
                        self._write(game_id, config)
                else:
                    run([config["runner"], config["entrypoint"]] + config["args"],
                        Path(config["entrypoint"]).parent)
                    run([config["wineserver"], "-w"], directory)
        except (ConfigError, OSError, subprocess.SubprocessError) as exc:
            error = str(exc)
            with (directory / "activity.log").open("a") as log:
                log.write(f"\nERROR: {error}\n")
                try:
                    subprocess.run([config["wineserver"], "-k"], env=env,
                                   stdout=log, stderr=subprocess.STDOUT,
                                   check=True, timeout=15)
                except (OSError, subprocess.SubprocessError) as cleanup_error:
                    error += f"; prefix shutdown failed: {cleanup_error}"
                    log.write(f"\nERROR: {cleanup_error}\n")
        finally:
            with self.lock:
                if self.jobs[game_id].get("cancelled"):
                    error = "operation stopped by user"
                self.jobs[game_id] = {"status": "failed" if error else "finished",
                                     "action": action, "error": error}
                self.active = None
                self.process = None

    def save(self, game_id, accepted, menu=True, desktop=False):
        if not isinstance(menu, bool) or not isinstance(desktop, bool):
            raise ConfigError("shortcut choices must be true or false")
        with self.lock:
            if self.active:
                raise ConfigError("close the game/installer before saving")
            config = self._read(game_id)
            job = self.jobs.get(game_id, {})
            if (accepted is not True or job.get("action") != "test"
                    or job.get("status") != "finished"):
                raise ConfigError("complete a test and confirm gameplay works before saving")
            if desktop and not desktop_folder():
                raise ConfigError("no desktop folder was found for a desktop shortcut")
            env = self._environment(game_id, config)
            directory = self._directory(game_id)
            script = directory / "play.sh"
            assignments = {key: env[key] for key in ("WINEPREFIX", "WINESERVER", "WINEDEBUG")}
            if config["gpu"]:
                assignments["DXVK_FILTER_DEVICE_NAME"] = config["gpu"]
            text = "#!/usr/bin/env bash\nset -euo pipefail\n"
            text += "unset WINEARCH WINEDLLOVERRIDES WINELOADER DXVK_FILTER_DEVICE_NAME\n"
            text += "".join(f"export {key}={shlex.quote(value)}\n" for key, value in assignments.items())
            text += f"cd -- {shlex.quote(str(Path(config['entrypoint']).parent))}\n"
            text += "exec " + shlex.join([config["runner"], config["entrypoint"]] + config["args"]) + ' "$@"\n'
            script.write_text(text)
            script.chmod(0o755)
            config["accepted"] = True
            config["launcher"] = str(script)
            self._apply_shortcuts(game_id, config, menu, desktop)
            self._write(game_id, config)
            return {"launcher": str(script), "desktop": config["desktop"],
                    "desktopShortcut": config["desktopShortcut"]}

    def shortcuts(self, game_id, menu, desktop):
        if not isinstance(menu, bool) or not isinstance(desktop, bool):
            raise ConfigError("shortcut choices must be true or false")
        with self.lock:
            config = self._read(game_id)
            if not config.get("accepted") or not Path(config.get("launcher") or "").is_file():
                raise ConfigError("save the game after a successful test before adding shortcuts")
            if desktop and not desktop_folder():
                raise ConfigError("no desktop folder was found for a desktop shortcut")
            self._apply_shortcuts(game_id, config, menu, desktop)
            self._write(game_id, config)
            return self._shortcut_status(config)

    @staticmethod
    def _shortcut_status(config):
        def present(key):
            value = config.get(key) or ""
            return bool(value) and Path(value).is_file()

        folder = desktop_folder()
        return {"menu": present("desktop"), "desktop": present("desktopShortcut"),
                "desktopFolder": str(folder) if folder else ""}

    def _apply_shortcuts(self, game_id, config, menu, desktop):
        # Desktop Exec has its own quoting rules, independent of shell quoting.
        quoted = config["launcher"].replace("\\", "\\\\").replace('"', '\\"').replace(
            "`", "\\`").replace("$", "\\$").replace("%", "%%")
        name = config["name"].replace("\\", "\\\\")
        entry = (f'[Desktop Entry]\nType=Application\nName={name}\n'
                 f'Exec="{quoted}"\nTerminal=false\nCategories=Game;\n')
        filename = f"winebundle-{game_id}.desktop"
        for key, wanted, folder in (("desktop", menu, applications_folder),
                                    ("desktopShortcut", desktop, desktop_folder)):
            previous = Path(config.get(key) or "")
            if previous.name == filename and previous.is_file():
                previous.unlink()
            config[key] = ""
            if not wanted:
                continue
            target = folder()
            if key == "desktop":
                target.mkdir(parents=True, exist_ok=True)
            path = target / filename
            path.write_text(entry)
            # Desktop icons only launch when executable; GNOME also needs them trusted.
            path.chmod(0o755 if key == "desktopShortcut" else 0o644)
            if key == "desktopShortcut":
                self._trust(path)
            config[key] = str(path)

    @staticmethod
    def _trust(path):
        gio = shutil.which("gio")
        if not gio:
            return
        # Snap apps (such as VS Code) can point GIO at modules without metadata support.
        env = {key: value for key, value in os.environ.items() if key != "GIO_MODULE_DIR"}
        try:
            subprocess.run([gio, "set", "-t", "string", str(path), "metadata::trusted", "true"],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, env=env, timeout=5, check=False)
        except (OSError, subprocess.SubprocessError):
            pass
