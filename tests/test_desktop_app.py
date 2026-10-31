import http.client
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gui"))
import winebundle_app  # noqa: E402
from local_setup import LocalSetup  # noqa: E402


class MenuEntryTests(unittest.TestCase):
    def test_entry_quotes_launcher_and_matches_app_id(self):
        entry = winebundle_app.desktop_entry(Path('/opt/my "games"/$bin/winebundle'))
        self.assertIn('Exec="/opt/my \\"games\\"/\\$bin/winebundle"\n', entry)
        self.assertIn(f"Icon={winebundle_app.APP_ID}\n", entry)
        self.assertIn(f"StartupWMClass={winebundle_app.APP_ID}\n", entry)
        self.assertIn("Categories=Game;\n", entry)

    def test_install_and_remove_menu_entry(self):
        with tempfile.TemporaryDirectory() as temporary:
            entry, icon = winebundle_app.install_menu_entry(temporary, Path("/opt/winebundle"))
            self.assertEqual(entry, Path(temporary) / "applications" /
                             f"{winebundle_app.APP_ID}.desktop")
            self.assertEqual(entry.stat().st_mode & 0o777, 0o644)
            self.assertIn('Exec="/opt/winebundle"', entry.read_text())
            self.assertEqual(icon.read_bytes(), winebundle_app.ICON.read_bytes())
            self.assertEqual(winebundle_app.install_menu_entry(temporary)[0], entry)
            self.assertEqual(sorted(winebundle_app.remove_menu_entry(temporary)),
                             sorted([entry, icon]))
            self.assertEqual(winebundle_app.remove_menu_entry(temporary), [])

    def test_launcher_is_executable(self):
        self.assertTrue(os.access(winebundle_app.LAUNCHER, os.X_OK))


    def test_package_install_does_not_add_a_second_menu_entry(self):
        self.assertTrue(winebundle_app.system_install(Path("/usr/share/winebundle")))
        self.assertFalse(winebundle_app.system_install(Path("/home/me/winebundle")))
        output = StringIO()
        with mock.patch.object(winebundle_app, "system_install", return_value=True), \
                mock.patch.object(winebundle_app, "install_menu_entry") as install, \
                redirect_stdout(output):
            self.assertEqual(winebundle_app.main(["--install-menu-entry"]), 0)
        install.assert_not_called()
        self.assertIn("already in the application menu", output.getvalue())


@unittest.skipUnless(shutil.which("dpkg-deb"), "dpkg-deb is not installed")
class DebPackageTests(unittest.TestCase):
    def test_builds_installable_package(self):
        root = Path(__file__).resolve().parents[1]
        version = (root / "VERSION").read_text().strip()
        with tempfile.TemporaryDirectory() as temporary:
            subprocess.run([str(root / "packaging/build-deb.sh"), temporary], check=True,
                           capture_output=True, text=True)
            deb = Path(temporary) / f"winebundle_{version}_all.deb"
            field = lambda name: subprocess.run(  # noqa: E731
                ["dpkg-deb", "-f", str(deb), name], check=True, capture_output=True,
                text=True).stdout.strip()
            self.assertEqual(field("Package"), "winebundle")
            self.assertEqual(field("Version"), version)
            self.assertIn("gir1.2-webkit-6.0 | gir1.2-webkit2-4.1", field("Depends"))
            tree = Path(temporary) / "tree"
            subprocess.run(["dpkg-deb", "-x", str(deb), str(tree)], check=True)
            launcher = tree / "usr/bin/winebundle"
            self.assertEqual(os.readlink(launcher), "../share/winebundle/bin/winebundle")
            self.assertTrue(os.access(launcher, os.X_OK))
            entry = tree / "usr/share/applications" / f"{winebundle_app.APP_ID}.desktop"
            self.assertIn('Exec="/usr/bin/winebundle"\n', entry.read_text())
            for path in ("usr/share/icons/hicolor/scalable/apps",
                         "usr/share/metainfo", "usr/share/winebundle/gui/static"):
                self.assertTrue(any((tree / path).iterdir()), path)
            self.assertFalse(list(tree.rglob("__pycache__")))
            self.assertFalse((tree / "usr/share/winebundle/tests").exists())


class SandboxTests(unittest.TestCase):
    def test_sandbox_blocked_only_by_ubuntu_userns_restriction(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.assertFalse(winebundle_app.webkit_sandbox_blocked(root))
            setting = root / "proc/sys/kernel/apparmor_restrict_unprivileged_userns"
            setting.parent.mkdir(parents=True)
            setting.write_text("0\n")
            self.assertFalse(winebundle_app.webkit_sandbox_blocked(root))
            setting.write_text("1\n")
            self.assertTrue(winebundle_app.webkit_sandbox_blocked(root))
            profile = root / "etc/apparmor.d/bwrap-userns-restrict"
            profile.parent.mkdir(parents=True)
            profile.write_text("")
            self.assertFalse(winebundle_app.webkit_sandbox_blocked(root))


class CloseWarningTests(unittest.TestCase):
    def test_only_install_and_test_steps_warn(self):
        self.assertIsNone(winebundle_app.close_warning(None))
        self.assertIsNone(winebundle_app.close_warning({"action": "play", "name": "Game"}))
        title, detail = winebundle_app.close_warning({"action": "prepare", "name": "Game"})
        self.assertEqual(title, "Installing Game is still running")
        self.assertIn("repeat", detail)
        self.assertEqual(winebundle_app.close_warning({"action": "test", "name": "Game"})[0],
                         "Testing Game is still running")


class FileDropTests(unittest.TestCase):
    def test_drop_passes_local_paths_to_the_page_as_json(self):
        files = [mock.Mock(get_path=lambda: '/home/me/Downloads/it\'s "a" game.exe'),
                 mock.Mock(get_path=lambda: None)]
        paths = winebundle_app.dropped_paths(files)
        self.assertEqual(paths, ['/home/me/Downloads/it\'s "a" game.exe'])
        script = winebundle_app.drop_script("files", paths)
        self.assertEqual(script, "window.winebundleDrop && window.winebundleDrop.files("
                         + json.dumps(paths) + ");")
        self.assertEqual(winebundle_app.drop_script("hover", True),
                         "window.winebundleDrop && window.winebundleDrop.hover(true);")


class AppServerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.server, self.url = winebundle_app.start_server()
        self.server.local = LocalSetup(self.temporary.name)
        self.port = self.server.server_port

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.temporary.cleanup()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.request(method, path, body=json.dumps(body) if body is not None else None,
                           headers=headers or {})
        response = connection.getresponse()
        content = response.read()
        connection.close()
        return response.status, content

    def test_serves_library_on_random_loopback_port(self):
        self.assertEqual(self.url, f"http://127.0.0.1:{self.port}/")
        status, content = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"local.js", content)

    def test_rejects_foreign_host_header(self):
        status, _ = self.request("GET", "/api/session", headers={"Host": "attacker.example"})
        self.assertEqual(status, 403)
        status, _ = self.request("GET", "/api/session",
                                 headers={"Host": f"localhost:{self.port}",
                                          "X-Winebundle-Token": self.server.token})
        self.assertEqual(status, 200)

    def test_app_does_not_serve_the_vm_builder(self):
        token = {"X-Winebundle-Token": self.server.token, "Content-Type": "application/json"}
        status, content = self.request("GET", "/api/session", headers=token)
        self.assertFalse(json.loads(content)["builder"])
        for path in ("/builder", "/app.js", "/api/jobs/x"):
            with self.subTest(path=path):
                self.assertEqual(self.request("GET", path, headers=token)[0], 404)
        for path in ("/api/jobs", "/api/config/validate"):
            with self.subTest(path=path):
                self.assertEqual(self.request("POST", path, {"config": {}}, token)[0], 404)
        status, content = self.request("GET", "/")
        self.assertNotIn(b"Advanced: VM bundle builder", content)

    def test_preferences_round_trip(self):
        token = {"X-Winebundle-Token": self.server.token, "Content-Type": "application/json"}
        status, _ = self.request("GET", "/api/preferences")
        self.assertEqual(status, 403)
        status, content = self.request("GET", "/api/preferences", headers=token)
        self.assertEqual(json.loads(content), {"systemChecked": False, "pickerFolders": {}})
        status, content = self.request("POST", "/api/preferences",
                                       {"systemChecked": True,
                                        "pickerFolders": {"exe": "/games"}}, token)
        self.assertEqual(status, 200)
        status, content = self.request("GET", "/api/preferences", headers=token)
        self.assertEqual(json.loads(content),
                         {"systemChecked": True, "pickerFolders": {"exe": "/games"}})
        status, _ = self.request("POST", "/api/preferences", {"theme": "dark"}, token)
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
