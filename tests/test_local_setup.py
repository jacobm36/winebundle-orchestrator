import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import local_setup
from local_setup import LocalSetup, browse, check_runner
from winebundle_config import ConfigError


FAKE_WINE = """#!/usr/bin/env python3
import json, os, pathlib, sys, time
p = pathlib.Path(os.environ["WINEPREFIX"])
if sys.argv[1:2] == ["--version"]:
    print("wine-test-1")
    sys.exit(0)
if sys.argv[1:2] == ["wineboot"]:
    (p / "drive_c/Program Files/Test").mkdir(parents=True)
    for folder in ("system32", "syswow64"):
        (p / "drive_c/windows" / folder).mkdir(parents=True)
    shared = p.parent / "shared-runner.dll"
    shared.write_text("runner")
    (p / "drive_c/windows/system32/d3d11.dll").symlink_to(shared)
    (p / "drive_c/Program Files/Test/game.exe").write_text("game")
elif sys.argv[1:2] == ["reg"]:
    pass
else:
    (p.parent / "invocation.json").write_text(json.dumps({
        "args": sys.argv[1:], "cwd": os.getcwd(),
        "prefix": str(p), "gpu": os.environ.get("DXVK_FILTER_DEVICE_NAME", "")
    }))
    if "--wait" in sys.argv: time.sleep(20)
    if "--fail" in sys.argv: sys.exit(7)
"""


class LocalSetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="winebundle test ")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.store = LocalSetup(self.base / "setups")
        self.runner = self.base / "wine"
        self.runner.write_text(FAKE_WINE)
        self.runner.chmod(0o755)
        self.server = self.base / "wineserver"
        self.server.write_text("#!/bin/sh\nexit 0\n")
        self.server.chmod(0o755)
        self.game = self.base / "game.exe"
        self.game.write_text("game")

    def create(self, **changes):
        return self.store.create({**dict(name="My game", runner=str(self.runner),
                                         mode="existing", source=str(self.game)), **changes})

    def finish(self, game_id):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            state = self.store.get(game_id)
            if state["status"] != "running":
                return state
            time.sleep(0.02)
        self.fail("local operation did not finish")

    def prepare(self, game_id):
        self.store.start(game_id, "prepare", {})
        state = self.finish(game_id)
        self.assertEqual(state["status"], "finished", state)
        return state

    def test_existing_game_to_persistent_launcher(self):
        game_id = self.create(gpu="Intel")
        state = self.prepare(game_id)
        self.assertTrue(state["config"]["prepared"])
        self.assertEqual(state["config"]["runnerVersion"], "wine-test-1")
        self.assertEqual(len(state["executables"]), 2)
        self.assertEqual(state["executables"][0], str(self.game))
        self.assertEqual(state["recommendedExecutable"], str(self.game))
        self.store.start(game_id, "test", {"entrypoint": str(self.game), "args": ["a b", "$(false)"]})
        self.assertEqual(self.finish(game_id)["status"], "finished")
        with patch.dict(os.environ, {"XDG_DATA_HOME": str(self.base / "data")}):
            result = self.store.save(game_id, True)
        subprocess.run([result["launcher"], "extra"], check=True)
        invocation = json.loads((self.store._directory(game_id) / "invocation.json").read_text())
        self.assertEqual(invocation["args"], [str(self.game), "a b", "$(false)", "extra"])
        self.assertEqual(invocation["gpu"], "Intel")
        self.assertEqual(invocation["cwd"], str(self.base))
        self.assertIn('Exec="', Path(result["desktop"]).read_text())
        restored = LocalSetup(self.store.root)
        self.assertTrue(restored.list()[0]["accepted"])
        self.assertTrue(restored.get(game_id)["config"]["prepared"])

    def test_installer_and_dxvk(self):
        dxvk = self.base / "dxvk"
        for architecture in ("x64", "x32"):
            (dxvk / architecture).mkdir(parents=True)
            for dll in ("d3d11.dll", "dxgi.dll"):
                (dxvk / architecture / dll).write_text(architecture)
        game_id = self.store.create(dict(
            name="Installer", runner=str(self.runner), mode="installer",
            source=str(self.game), installArgs=["/S"], dxvk=str(dxvk)))
        self.prepare(game_id)
        directory = self.store._directory(game_id)
        invocation = json.loads((directory / "invocation.json").read_text())
        self.assertEqual(invocation["args"], [str(self.game), "/S"])
        self.assertEqual((directory / "prefix/drive_c/windows/system32/d3d11.dll").read_text(), "x64")
        self.assertEqual((directory / "shared-runner.dll").read_text(), "runner")

    def saved_game(self):
        game_id = self.create()
        self.prepare(game_id)
        self.store.start(game_id, "test", {"entrypoint": str(self.game), "args": ["saved argument"]})
        self.assertEqual(self.finish(game_id)["status"], "finished")
        with patch.dict(os.environ, {"XDG_DATA_HOME": str(self.base / "data")}):
            self.store.save(game_id, True)
        return game_id

    def test_shortcuts_can_be_chosen_on_save_and_changed_later(self):
        home = self.base / "home"
        desktop = home / "Bureau"
        desktop.mkdir(parents=True)
        (home / ".config").mkdir()
        (home / ".config/user-dirs.dirs").write_text('XDG_DESKTOP_DIR="$HOME/Bureau"\n')
        environment = {"HOME": str(home), "XDG_DATA_HOME": str(self.base / "data"),
                       "XDG_CONFIG_HOME": ""}
        game_id = self.create()
        self.prepare(game_id)
        self.store.start(game_id, "test", {"entrypoint": str(self.game), "args": []})
        self.assertEqual(self.finish(game_id)["status"], "finished")
        menu = self.base / f"data/applications/winebundle-{game_id}.desktop"
        icon = desktop / f"winebundle-{game_id}.desktop"
        with patch.dict(os.environ, environment), \
                patch.object(LocalSetup, "_trust") as trust:
            with self.assertRaises(ConfigError):
                self.store.shortcuts(game_id, True, True)
            with self.assertRaises(ConfigError):
                self.store.save(game_id, True, "yes", False)
            result = self.store.save(game_id, True, menu=False, desktop=True)
            self.assertFalse(menu.exists())
            self.assertEqual(result["desktopShortcut"], str(icon))
            self.assertIn("Exec=", icon.read_text())
            self.assertTrue(os.access(icon, os.X_OK))
            trust.assert_called_once_with(icon)
            self.assertEqual(self.store.get(game_id)["shortcuts"],
                             {"menu": False, "desktop": True, "desktopFolder": str(desktop)})
            status = self.store.shortcuts(game_id, True, False)
            self.assertEqual(status, {"menu": True, "desktop": False, "desktopFolder": str(desktop)})
            self.assertTrue(menu.is_file())
            self.assertFalse(icon.exists())
            self.store.shortcuts(game_id, False, False)
            self.assertFalse(menu.exists())
            config = self.store.get(game_id)["config"]
            self.assertEqual((config["desktop"], config["desktopShortcut"]), ("", ""))
            self.assertTrue(config["accepted"])
            (home / ".config/user-dirs.dirs").write_text('XDG_DESKTOP_DIR="$HOME/"\n')
            self.assertIsNone(local_setup.desktop_folder())
            with self.assertRaises(ConfigError):
                self.store.shortcuts(game_id, False, True)

    def test_play_saved_game_after_restart_preserves_confirmation_and_arguments(self):
        game_id = self.saved_game()
        self.store = LocalSetup(self.store.root)
        before = self.store._read(game_id)
        self.store.start(game_id, "play", {"entrypoint": "/ignored.exe", "args": ["--fail"]})
        state = self.finish(game_id)
        self.assertEqual(state["status"], "finished", state)
        self.assertEqual(state["action"], "play")
        self.assertEqual(state["config"], before)
        invocation = json.loads((self.store._directory(game_id) / "invocation.json").read_text())
        self.assertEqual(invocation["args"], [str(self.game), "saved argument"])
        self.assertEqual(self.store.list()[0]["action"], "play")
        with self.assertRaisesRegex(ConfigError, "complete a test"):
            self.store.save(game_id, True)

    def test_play_requires_preparation_and_gameplay_confirmation(self):
        game_id = self.create()
        with self.assertRaisesRegex(ConfigError, "test gameplay and save"):
            self.store.start(game_id, "play", {})
        self.prepare(game_id)
        with self.assertRaisesRegex(ConfigError, "test gameplay and save"):
            self.store.start(game_id, "play", {})
        self.store.start(game_id, "test", {"entrypoint": str(self.game), "args": []})
        self.finish(game_id)
        with self.assertRaisesRegex(ConfigError, "test gameplay and save"):
            self.store.start(game_id, "play", {})

    def test_play_reports_missing_game_and_runner(self):
        game_id = self.saved_game()
        self.game.unlink()
        with self.assertRaisesRegex(ConfigError, "game EXE is missing"):
            self.store.start(game_id, "play", {})
        self.game.write_text("game")
        self.runner.unlink()
        with self.assertRaisesRegex(ConfigError, "Wine runner is missing"):
            self.store.start(game_id, "play", {})
        self.assertTrue(self.store._read(game_id)["accepted"])
        self.assertIsNone(self.store.active)

    def test_library_exposes_running_and_failed_operations(self):
        game_id = self.saved_game()
        config = self.store._read(game_id)
        config["args"] = ["--wait"]
        self.store._write(game_id, config)
        self.store.start(game_id, "play", {})
        self.assertEqual(self.store.list()[0]["status"], "running")
        self.assertEqual(self.store.list()[0]["action"], "play")
        with self.assertRaisesRegex(ConfigError, "another local operation"):
            self.store.start(game_id, "play", {})
        self.store.stop(game_id)
        state = self.finish(game_id)
        self.assertEqual(state["status"], "failed")
        self.assertIn("stopped by user", self.store.list()[0]["error"])
        self.assertTrue(state["config"]["accepted"])

    def test_failed_play_does_not_remove_saved_game(self):
        game_id = self.saved_game()
        config = self.store._read(game_id)
        config["args"] = ["--fail"]
        self.store._write(game_id, config)
        self.store.start(game_id, "play", {})
        state = self.finish(game_id)
        self.assertEqual(state["status"], "failed")
        self.assertTrue(state["config"]["accepted"])
        self.assertIn("7", state["error"])
        self.assertIn("ERROR:", state["log"])

    def test_msi_uses_msiexec(self):
        installer = self.base / "setup.msi"
        installer.write_text("installer")
        game_id = self.store.create(dict(
            name="MSI", runner=str(self.runner), mode="installer",
            source=str(installer), installArgs=["/qn"]))
        self.prepare(game_id)
        invocation = json.loads((self.store._directory(game_id) / "invocation.json").read_text())
        self.assertEqual(invocation["args"], ["msiexec", "/i", str(installer), "/qn"])

    def installer_setup(self, name="Blasphemous"):
        game_id = self.store.create(dict(
            name=name, runner=str(self.runner), mode="installer", source=str(self.game)))
        return game_id, self.store._directory(game_id) / "prefix/drive_c"

    def executable(self, drive, relative):
        path = drive / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("game")
        return str(path)

    def test_discovers_custom_locations_and_ranks_game_before_helpers(self):
        game_id, drive = self.installer_setup()
        game = self.executable(drive, "GOG Games/Blasphemous/Blasphemous.EXE")
        helpers = [
            self.executable(drive, "GOG Games/Blasphemous/unins000.exe"),
            self.executable(drive, "GOG Games/Blasphemous/UnityCrashHandler64.exe"),
            self.executable(drive, "GOG Games/Blasphemous/_CommonRedist/setup.exe"),
        ]
        custom = self.executable(drive, "Games/Other/other.exe")
        appdata = self.executable(drive, "users/player/AppData/Local/Other/other.exe")
        system = self.executable(drive, "Windows/system32/notepad.exe")
        state = self.store.get(game_id)
        self.assertEqual(state["executables"][0], game)
        self.assertEqual(state["recommendedExecutable"], game)
        self.assertTrue(set(helpers + [custom, appdata]).issubset(state["executables"]))
        self.assertNotIn(system, state["executables"])
        self.assertLess(state["executables"].index(custom),
                        state["executables"].index(helpers[0]))

    def test_shipping_executable_matches_normalized_game_title(self):
        game_id, drive = self.installer_setup(name="My Great Game")
        game = self.executable(
            drive, "Games/MyGreatGame/Binaries/Win64/MyGreatGame-Win64-Shipping.exe")
        self.executable(drive, "Games/MyGreatGame/Launcher.exe")
        self.assertEqual(self.store.get(game_id)["recommendedExecutable"], game)

    def test_ambiguous_candidates_are_not_recommended(self):
        game_id, drive = self.installer_setup()
        paths = [
            self.executable(drive, "Games/Blasphemous/bin32/Blasphemous.exe"),
            self.executable(drive, "Games/Blasphemous/bin64/Blasphemous.exe"),
        ]
        state = self.store.get(game_id)
        self.assertEqual(state["recommendedExecutable"], "")
        self.assertEqual(state["executables"], sorted(paths))

    def test_helpers_alone_are_not_recommended(self):
        game_id, drive = self.installer_setup(name="Setup")
        self.executable(drive, "GOG Games/Setup/setup.exe")
        self.assertEqual(self.store.get(game_id)["recommendedExecutable"], "")

    def test_scan_does_not_follow_directory_symlinks(self):
        game_id, drive = self.installer_setup()
        external = self.base / "external"
        self.executable(external, "Blasphemous.exe")
        drive.mkdir(parents=True)
        (drive / "outside").symlink_to(external, target_is_directory=True)
        (drive / "loop").symlink_to(drive, target_is_directory=True)
        self.assertEqual(self.store.get(game_id)["executables"], [])

    def test_limit_is_applied_after_ranking(self):
        game_id, drive = self.installer_setup()
        for index in range(305):
            self.executable(drive, f"Program Files/Helpers/tool{index:03}.exe")
        game = self.executable(drive, "GOG Games/Blasphemous/Blasphemous.exe")
        state = self.store.get(game_id)
        self.assertEqual(len(state["executables"]), 300)
        self.assertEqual(state["executables"][0], game)
        self.assertEqual(state["recommendedExecutable"], game)

    def test_detection_does_not_change_saved_entrypoint(self):
        game_id, drive = self.installer_setup()
        self.executable(drive, "GOG Games/Blasphemous/Blasphemous.exe")
        self.store.get(game_id)
        self.assertEqual(self.store._read(game_id)["entrypoint"], "")
        self.store.jobs[game_id] = {"status": "running", "error": ""}
        with patch("local_setup.os.walk", side_effect=AssertionError("scan while running")):
            state = self.store.get(game_id)
        self.assertEqual(state["executables"], [])
        self.assertEqual(state["recommendedExecutable"], "")

    def test_scan_errors_are_reported(self):
        game_id, drive = self.installer_setup()
        drive.mkdir(parents=True)

        def denied(*args, **kwargs):
            kwargs["onerror"](PermissionError("access denied"))
            return iter(())

        with patch("local_setup.os.walk", side_effect=denied):
            with self.assertRaisesRegex(ConfigError, "cannot scan installed executables"):
                self.store.get(game_id)

    def test_rejects_bad_inputs_and_unverified_save(self):
        for value in ("../escape", "", 123):
            with self.assertRaises(ConfigError):
                self.store.get(value)
        with self.assertRaises(ConfigError):
            self.create(arch="bad")
        game_id = self.create()
        with self.assertRaises(ConfigError):
            self.store.start(game_id, "test", {"entrypoint": str(self.game)})
        self.prepare(game_id)
        with self.assertRaises(ConfigError):
            self.store.save(game_id, True)
        self.store.start(game_id, "test", {"entrypoint": str(self.game), "args": ["--fail"]})
        state = self.finish(game_id)
        self.assertEqual(state["status"], "failed")
        self.assertIn("7", state["error"])
        with self.assertRaises(ConfigError):
            self.store.save(game_id, True)

    def test_overlapping_jobs_and_stop(self):
        game_id = self.create()
        self.prepare(game_id)
        self.store.start(game_id, "test", {"entrypoint": str(self.game), "args": ["--wait"]})
        with self.assertRaises(ConfigError):
            self.create()
        self.store.stop(game_id)
        self.assertEqual(self.finish(game_id)["status"], "failed")
        with self.assertRaises(ConfigError):
            self.store.save(game_id, True)

    def test_new_setups_use_readable_unique_folder_names(self):
        first = self.create(name="Blazing Chrome: Deluxe!")
        second = self.create(name="Blazing Chrome: Deluxe!")
        symbols = self.create(name="\u9b3c\u6ce3")
        self.assertEqual((first, second, symbols), ("blazing-chrome-deluxe", "blazing-chrome-deluxe-2", "game"))
        self.assertEqual(self.store.get(first)["directory"], str(self.store.root / first))
        self.assertEqual(self.store.get(first)["drive"], str(self.store.root / first / "prefix/drive_c"))
        with patch.dict(os.environ, {"XDG_DATA_HOME": str(self.base / "data")}):
            self.store.start(first, "prepare", {})
            self.finish(first)
            self.store.start(first, "test", {"entrypoint": str(self.game), "args": []})
            self.finish(first)
            result = self.store.save(first, True)
        self.assertEqual(Path(result["desktop"]).name, "winebundle-blazing-chrome-deluxe.desktop")

    def test_existing_hex_setups_remain_available_and_stray_folders_are_ignored(self):
        legacy = "142cf98d200c47b9b2cab142c13b148f"
        (self.store.root / legacy).mkdir(parents=True)
        config = dict(name="Legacy", id=legacy, runner=str(self.runner), wineserver=str(self.server),
                      mode="existing", source=str(self.game), arch="win64", installArgs=[],
                      gpu="", dxvk="", entrypoint=str(self.game), args=[], prepared=True, accepted=True)
        self.store._write(legacy, config)
        (self.store.root / "notes").mkdir()
        (self.store.root / "Not Valid").mkdir()
        self.assertEqual([setup["id"] for setup in self.store.list()], [legacy])
        self.assertEqual(self.store.get(legacy)["config"]["name"], "Legacy")
        for bad in ("../escape", "UPPER", "a" * 65, "-leading", "trailing-", "a--b"):
            with self.subTest(bad=bad), self.assertRaisesRegex(ConfigError, "invalid local game ID"):
                self.store.get(bad)

    def test_browse_filters_by_kind_and_hides_dotfiles(self):
        folder = self.base / "browse"
        (folder / "Sub Folder").mkdir(parents=True)
        (folder / ".hidden").mkdir()
        for name in ("Setup.EXE", "app.msi", "readme.txt", ".secret.exe"):
            (folder / name).write_text("x")
        (folder / "tool").write_text("#!/bin/sh\n")
        (folder / "tool").chmod(0o755)
        names = lambda result: [entry["name"] for entry in result["entries"]]
        self.assertEqual(names(browse(str(folder), "installer")), ["Sub Folder", "app.msi", "Setup.EXE"])
        self.assertEqual(names(browse(str(folder), "exe")), ["Sub Folder", "Setup.EXE"])
        self.assertEqual(names(browse(str(folder), "runner")), ["Sub Folder", "tool"])
        self.assertEqual(names(browse(str(folder), "folder")), ["Sub Folder"])
        self.assertEqual(names(browse(str(folder), "exe", hidden=True)),
                         [".hidden", "Sub Folder", ".secret.exe", "Setup.EXE"])
        result = browse(str(folder / "Sub Folder" / ".."), "folder")
        self.assertEqual(result["path"], str(folder))
        self.assertEqual(result["parent"], str(self.base))
        self.assertEqual(browse("/", "folder")["parent"], "")
        for path, kind, pattern in (("relative", "exe", "absolute"), (str(folder / "missing"), "exe", "not found"),
                                    (str(folder / "readme.txt"), "exe", "not found"),
                                    (str(folder), "anything", "unknown"), ("a\0b", "exe", "text")):
            with self.subTest(path=path), self.assertRaisesRegex(ConfigError, pattern):
                browse(path, kind)

    def test_browse_limits_large_folders(self):
        folder = self.base / "large"
        folder.mkdir()
        for index in range(5):
            (folder / f"{index}.exe").write_text("x")
        with patch.object(local_setup, "MAX_BROWSE_ENTRIES", 3):
            result = browse(str(folder), "exe")
        self.assertEqual(len(result["entries"]), 3)
        self.assertTrue(result["truncated"])

    def test_runner_check_and_default(self):
        self.assertEqual(check_runner(str(self.runner)), {"error": ""})
        proton = self.base / "proton"
        proton.write_text("#!/bin/sh\n")
        proton.chmod(0o755)
        lonely = self.base / "lonely"
        lonely.mkdir()
        (lonely / "wine").write_text("#!/bin/sh\n")
        (lonely / "wine").chmod(0o755)
        for value, pattern in (("", "absolute"), ("wine", "absolute"), (str(self.base), "folder"),
                               (str(self.game), "No executable"), (str(proton), "Proton"),
                               (str(lonely / "wine"), "wineserver")):
            with self.subTest(value=value):
                self.assertRegex(check_runner(value)["error"], pattern)
        with patch.object(local_setup.shutil, "which", return_value=str(self.runner)), \
                patch.object(local_setup, "check_runner",
                             side_effect=lambda path: {"error": "" if path == str(self.runner) else "no"}):
            self.assertEqual(local_setup.default_runner(), str(self.runner))
        with patch.object(local_setup.shutil, "which", return_value=None), \
                patch.object(local_setup, "check_runner", return_value={"error": "no"}):
            self.assertEqual(local_setup.default_runner(), "")


if __name__ == "__main__":
    unittest.main()
