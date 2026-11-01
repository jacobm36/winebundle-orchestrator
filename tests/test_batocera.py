import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import batocera
import system_check
from local_setup import LocalSetup
from winebundle_config import ConfigError
from test_local_setup import FAKE_WINE


def make_runner(folder, server=True):
    (folder / "bin").mkdir(parents=True)
    for name in ("wine", "wineserver") if server else ("wine",):
        path = folder / "bin" / name
        path.write_text("#!/bin/sh\n")
        path.chmod(0o755)
    return folder / "bin/wine"


class BatoceraHelperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="winebundle batocera ")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def test_detection(self):
        self.assertFalse(batocera.active(self.base))
        (self.base / "usr/share/batocera").mkdir(parents=True)
        (self.base / batocera.VERSION_FILE).write_text("43.1\n")
        self.assertTrue(batocera.active(self.base))

    def test_runners_list_what_batocera_can_launch_preferring_proton(self):
        system, custom = self.base / "usr/wine", self.base / "custom"
        make_runner(system / "wine-tkg")
        proton = make_runner(system / "wine-proton")
        make_runner(system / "wine-other")
        make_runner(system / "broken", server=False)
        (system / "dxvk").mkdir()
        mine = make_runner(custom / "wine-11.16")
        found = batocera.runners(system, custom)
        self.assertEqual([runner["name"] for runner in found],
                         ["wine-proton", "wine-tkg", "wine-11.16"])
        self.assertEqual(found[0]["path"], str(proton))
        self.assertTrue(found[2]["custom"])
        self.assertEqual(batocera.runner_name(proton, system, custom), "wine-proton")
        self.assertEqual(batocera.runner_name(mine, system, custom), "wine-11.16")
        self.assertEqual(batocera.runner_name(system / "wine-other/bin/wine", system, custom), "")
        self.assertEqual(batocera.runner_name("/usr/bin/wine", system, custom), "")
        self.assertEqual(batocera.runners(self.base / "missing", self.base / "missing"), [])

    def test_wine_environment_points_at_the_runner_libraries(self):
        runner = make_runner(self.base / "wine-proton")
        (self.base / "wine-proton/lib/wine").mkdir(parents=True)
        env = batocera.wine_environment(runner)
        self.assertIn(f"{self.base}/wine-proton/lib/wine/x86_64-unix", env["LD_LIBRARY_PATH"])
        self.assertTrue(env["LD_LIBRARY_PATH"].startswith("/lib32:"))
        self.assertEqual(env["WINEDLLPATH"], f"{self.base}/wine-proton/lib/wine/i386-windows:"
                                             f"{self.base}/wine-proton/lib/wine/x86_64-windows")

    def test_folder_names_are_safe_and_unique(self):
        self.assertEqual(batocera.folder_name('Half-Life: "Source" [GOTY]'), "Half-Life Source GOTY")
        self.assertEqual(batocera.folder_name("../../etc"), "etc")
        self.assertEqual(batocera.folder_name("Café & Co."), "Café & Co")
        self.assertEqual(batocera.folder_name("$`|"), "Windows game")
        roms = self.base / "roms"
        first = batocera.new_prefix("Quake", roms)
        self.assertEqual(first, roms / "Quake.wine")
        (roms / "Quake (2).wsquashfs").write_text("")
        self.assertEqual(batocera.new_prefix("Quake", roms), roms / "Quake (3).wine")
        self.assertTrue(batocera.managed_path(first, roms))
        self.assertFalse(batocera.managed_path(roms / "sub/Quake.wine", roms))
        self.assertFalse(batocera.managed_path(roms / "Quake.pc", roms))

    def test_autorun_runs_the_game_through_batocera_eval(self):
        prefix = self.base / "My Game.wine"
        folder = prefix / "drive_c/Program Files/Studio's Game"
        folder.mkdir(parents=True)
        text = batocera.autorun(prefix, folder / "Game $1.exe", ["-w", "a b"])
        self.assertTrue(text.startswith("DIR=drive_c/Program Files/Studio's Game\nCMD="))
        self.assertNotIn("\r", text)
        batocera.write_autorun(prefix, folder / "Game $1.exe", ["-w", "a b"])
        self.assertEqual((prefix / "autorun.cmd").read_text(), text)
        # The same steps batocera-wine takes: grep the values, cd into DIR, eval CMD.
        script = ('DIR=$(grep "^DIR=" "$1/autorun.cmd" | sed -e s+^DIR=++)\n'
                  'CMD=$(grep "^CMD=" "$1/autorun.cmd" | sed -e s+^CMD=++)\n'
                  'cd "$1/$DIR" && pwd && eval printf "\'%s\\\\n\'" ${CMD}\n')
        result = subprocess.run(["bash", "-c", script, "bash", str(prefix)], check=True,
                                capture_output=True, text=True)
        self.assertEqual(result.stdout.splitlines(),
                         [str(folder), "Game $1.exe", "-w", "a b"])
        outside = self.base / "Games/Old Game/game.exe"
        self.assertEqual(batocera.autorun(prefix, outside, [])[:25], "DIR=../Games/Old Game\nCMD")
        self.assertIn("DIR=\n", batocera.autorun(prefix, prefix / "game.exe", []))

    def test_set_runner_uses_batocera_settings(self):
        with patch("batocera.shutil.which", return_value="/usr/bin/batocera-settings-set"), \
                patch("batocera.subprocess.run") as run:
            run.return_value.returncode = 0
            self.assertTrue(batocera.set_runner("My Game.wine", "wine-proton"))
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0], ["/usr/bin/batocera-settings-set",
                                                 'windows["My Game.wine"].wine-runner',
                                                 "wine-proton"])
        with patch("batocera.shutil.which", return_value=None):
            self.assertFalse(batocera.set_runner("My Game.wine", "wine-proton"))
        self.assertFalse(batocera.set_runner("My Game.wine", ""))


class BatoceraSetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="winebundle batocera ")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        for target in ("graphics.discrete_gpu", "batocera.reload_games"):
            patcher = patch(target, return_value=None)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch("batocera.set_runner", return_value=True)
        self.set_runner = patcher.start()
        self.addCleanup(patcher.stop)
        self.store = LocalSetup(self.base / "setups", batocera_mode=True)
        self.store.roms = self.base / "roms"
        self.runner = self.base / "wine"
        self.runner.write_text(FAKE_WINE)
        self.runner.chmod(0o755)
        server = self.base / "wineserver"
        server.write_text("#!/bin/sh\nexit 0\n")
        server.chmod(0o755)
        self.installer = self.base / "setup_game.exe"
        self.installer.write_text("installer")

    def finish(self, game_id):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            state = self.store.get(game_id)
            if state["status"] != "running":
                return state
            time.sleep(0.02)
        self.fail("operation did not finish")

    def saved_game(self):
        game_id = self.store.create(dict(name="Doom: Eternal", runner=str(self.runner),
                                         mode="installer", source=str(self.installer)))
        prefix = self.store.roms / "Doom Eternal.wine"
        self.assertTrue(prefix.is_dir())
        self.store.start(game_id, "prepare", {})
        state = self.finish(game_id)
        self.assertEqual(state["status"], "finished", state)
        self.assertEqual(state["drive"], str(prefix / "drive_c"))
        game = prefix / "drive_c/Program Files/Test/game.exe"
        self.store.start(game_id, "test", {"entrypoint": str(game), "args": ["-safe"]})
        self.assertEqual(self.finish(game_id)["status"], "finished")
        return game_id, prefix

    def test_games_install_into_batocera_and_save_writes_autorun(self):
        game_id, prefix = self.saved_game()
        invocation = json.loads((self.store.roms / "invocation.json").read_text())
        self.assertEqual(invocation["prefix"], str(prefix))
        result = self.store.save(game_id, True)
        self.assertEqual(result["batocera"], "Doom Eternal")
        self.assertEqual((prefix / "autorun.cmd").read_text(),
                         "DIR=drive_c/Program Files/Test\nCMD=game.exe -safe\n")
        self.set_runner.assert_called_once_with("Doom Eternal.wine", "")
        batocera.reload_games.assert_called()
        state = self.store.get(game_id)
        self.assertTrue(state["config"]["accepted"])
        self.assertEqual(state["config"]["launcher"], "")
        self.assertFalse((self.store._directory(game_id) / "play.sh").exists())
        self.assertEqual(state["batocera"]["menuName"], "Doom Eternal")
        self.assertFalse(state["batocera"]["outside"])
        self.store.remove(game_id)
        self.assertFalse(prefix.exists())
        self.assertFalse(self.store._directory(game_id).exists())

    @unittest.skipUnless(shutil.which("mksquashfs") and shutil.which("unsquashfs"),
                         "squashfs tools not installed")
    def test_compress_replaces_the_folder_with_a_wsquashfs(self):
        game_id, prefix = self.saved_game()
        with self.assertRaisesRegex(ConfigError, "can be compressed"):
            self.store.start(game_id, "compress", {})
        self.store.save(game_id, True)
        self.assertTrue(self.store.get(game_id)["batocera"]["compressible"])
        self.store.start(game_id, "compress", {})
        state = self.finish(game_id)
        self.assertEqual(state["status"], "finished", state)
        target = self.store.roms / "Doom Eternal.wsquashfs"
        self.assertTrue(target.is_file())
        self.assertFalse(prefix.exists())
        self.assertEqual(state["config"]["compressed"], str(target))
        self.assertEqual(state["batocera"]["menuName"], "Doom Eternal")
        self.assertFalse(state["batocera"]["compressible"])
        self.set_runner.assert_called_with("Doom Eternal.wsquashfs", "")
        listing = subprocess.run(["unsquashfs", "-l", str(target)], check=True,
                                 capture_output=True, text=True).stdout
        self.assertIn("squashfs-root/autorun.cmd", listing)
        self.assertIn("squashfs-root/drive_c/Program Files/Test/game.exe", listing)
        for action in ("test", "play", "compress"):
            with self.assertRaisesRegex(ConfigError, "compressed"):
                self.store.start(game_id, action, {"entrypoint": "/x.exe"})
        self.store.remove(game_id)
        self.assertFalse(target.exists())

    def test_failed_compression_leaves_the_folder(self):
        game_id, prefix = self.saved_game()
        self.store.save(game_id, True)
        with patch("local_setup.shutil.which", return_value="/usr/bin/mksquashfs"), \
                patch("batocera.compress_command", return_value=["sh", "-c", "echo x > \"$0\"; exit 3",
                                                                 str(prefix.with_suffix(".wsquashfs"))]):
            self.store.start(game_id, "compress", {})
            state = self.finish(game_id)
        self.assertEqual(state["status"], "failed")
        self.assertTrue(prefix.is_dir())
        self.assertFalse(prefix.with_suffix(".wsquashfs").exists())
        self.assertNotIn("compressed", state["config"])

    def test_games_outside_their_folder_are_not_compressible(self):
        game = self.base / "Games/Old Game/game.exe"
        game.parent.mkdir(parents=True)
        game.write_text("game")
        game_id = self.store.create(dict(name="Old Game", runner=str(self.runner),
                                         mode="existing", source=str(game)))
        self.store.start(game_id, "prepare", {})
        self.finish(game_id)
        self.store.start(game_id, "test", {"entrypoint": str(game), "args": []})
        self.finish(game_id)
        self.store.save(game_id, True)
        prefix = self.store.roms / "Old Game.wine"
        self.assertEqual((prefix / "autorun.cmd").read_text(),
                         "DIR=../../Games/Old Game\nCMD=game.exe\n")
        status = self.store.get(game_id)["batocera"]
        self.assertTrue(status["outside"])
        self.assertFalse(status["compressible"])
        self.store.remove(game_id)
        self.assertTrue(game.is_file())

    def test_prefix_outside_the_games_folder_is_refused(self):
        with self.assertRaises(ConfigError):
            self.store._prefix("x", {"prefix": str(self.base / "elsewhere.wine")})
        with self.assertRaises(ConfigError):
            self.store._prefix("x", {"prefix": str(self.store.roms / "Game.pc")})


class BatoceraSystemCheckTests(unittest.TestCase):
    def test_batocera_skips_desktop_checks(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "usr/share/batocera").mkdir(parents=True)
            (root / batocera.VERSION_FILE).write_text("43.1\n")
            with patch("local_setup.default_runner", return_value=""), \
                    patch("graphics.discrete_gpu", return_value=None):
                result = system_check.run(root=root)
        self.assertEqual([check["id"] for check in result["checks"]],
                         ["wine", "wine32", "gpu", "disk", "batocera"])
        # Batocera has no package manager, so no install commands are suggested.
        self.assertFalse([check["id"] for check in result["checks"]
                          if (check.get("remedy") or {}).get("commands")])
        self.assertIn("Batocera", result["checks"][0]["detail"])


if __name__ == "__main__":
    unittest.main()
