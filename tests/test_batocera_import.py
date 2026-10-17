import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import batocera_import  # noqa: E402
from winebundle_config import load_config  # noqa: E402


class BatoceraImportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.userdata = Path(self.temporary.name) / "userdata"
        self.game = self.userdata / "roms" / "windows" / "Game.wine"
        (self.game / "drive_c" / "windows" / "syswow64").mkdir(parents=True)
        (self.game / "autorun.cmd").write_text(
            "DIR=drive_c/Program Files/Vendor/Game\nCMD=\"Game.exe\" -windowed\nENV=DXVK_HUD=1\n"
        )
        (self.game / "system.reg").write_text("WINE REGISTRY Version 2\n#arch=win32\n")
        (self.game / "winetricks.log").write_text("vcrun2019\nd3dx9\nvcrun2019\n")
        (self.game / "user.reg").write_text(
            '[Software\\\\Wine\\\\DllOverrides] 1700000000\n"d3d9"="native,builtin"\n\n'
            '[Software\\\\Other]\n"x"="y"\n'
        )
        conf = self.userdata / "system" / "batocera.conf"
        conf.parent.mkdir(parents=True)
        conf.write_text(
            'windows.wine-runner=wine-tkg\n'
            'windows["Game.wine"].dxvk=1\n'
            'windows["Other.wine"].dxvk=0\n'
        )
        self.destination = Path(self.temporary.name) / "game.json"

    def tearDown(self):
        self.temporary.cleanup()

    def run_import(self, *extra):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = batocera_import.main(
                [str(self.game), str(self.destination), "--installer", "/tmp/setup.exe", *extra]
            )
        return code, stdout.getvalue(), stderr.getvalue()

    def test_imports_prefix_game(self):
        code, output, _ = self.run_import("--install-arg", "/S")
        self.assertEqual(code, 0)
        config = load_config(self.destination)
        self.assertEqual(config["entrypoint"], r"C:\Program Files\Vendor\Game\Game.exe")
        self.assertEqual(config["arch"], "win32")
        self.assertEqual(config["winetricks"], ["vcrun2019", "d3dx9"])
        self.assertEqual(config["installArgs"], ["/S"])
        self.assertTrue(Path(config["output"]).is_absolute())
        self.assertIn("-windowed", output)
        self.assertIn("ENV not represented: DXVK_HUD=1", output)
        self.assertIn("wine-runner=wine-tkg", output)
        self.assertIn("dxvk=1", output)
        self.assertNotIn("dxvk=0", output)
        self.assertIn("d3d9=native,builtin", output)
        self.assertNotIn("x=y", output)

    def test_portable_game_uses_bottle_and_requires_entrypoint(self):
        portable = self.userdata / "roms" / "windows" / "Port.pc"
        portable.mkdir()
        (portable / "autorun.cmd").write_text("CMD=bin\\port.exe\n")
        bottle = self.userdata / "system" / "wine-bottles" / "windows" / "Port.wine"
        (bottle / "drive_c" / "windows" / "syswow64").mkdir(parents=True)
        self.game = portable

        code, _, error = self.run_import()
        self.assertEqual(code, 1)
        self.assertIn("'bin\\\\port.exe'", error)

        code, _, _ = self.run_import("--entrypoint", r"C:\Port\port.exe")
        self.assertEqual(code, 0)
        config = json.loads(self.destination.read_text())
        self.assertEqual(config["arch"], "win64")

    def test_refuses_overwrite_and_squashfs(self):
        self.destination.write_text("{}")
        code, _, error = self.run_import()
        self.assertEqual(code, 1)
        self.assertIn("--force", error)
        self.assertEqual(self.destination.read_text(), "{}")

        squash = self.game.with_name("Game.wsquashfs")
        squash.write_bytes(b"")
        self.game = squash
        code, _, error = self.run_import("--force")
        self.assertEqual(code, 1)
        self.assertIn("unsquashfs", error)


if __name__ == "__main__":
    unittest.main()
