import http.client
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "gui"))

from winebundle_config import (  # noqa: E402
    ConfigError,
    config_arguments,
    load_config,
    validate_config,
    write_config,
)
from winebundle_gui import RequestHandler, WinebundleServer  # noqa: E402
from local_setup import LocalSetup  # noqa: E402


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "version": 1,
            "installer": "setup.exe",
            "entrypoint": r"C:\Program Files\Test\test.exe",
            "output": "artifacts",
            "installArgs": ["/S"],
            "winetricks": ["corefonts"],
            "arch": "win64",
            "timeout": 240,
        }

    def test_round_trip_and_relative_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "build.json"
            write_config(path, self.config)
            loaded = load_config(path)
            arguments = config_arguments(loaded, path.parent)

        self.assertEqual(loaded, self.config)
        self.assertIn(str(path.parent / "setup.exe"), arguments)
        self.assertIn(str(path.parent / "artifacts"), arguments)
        self.assertEqual(arguments.count("--install-arg"), 1)

    def test_rejects_unknown_fields(self):
        self.config["unexpected"] = True
        with self.assertRaisesRegex(ConfigError, "unknown configuration field"):
            validate_config(self.config)

    def test_rejects_conflicting_validation(self):
        self.config["smokeScript"] = "smoke.sh"
        self.config["smokeArgs"] = ["--version"]
        with self.assertRaisesRegex(ConfigError, "cannot be combined"):
            validate_config(self.config)

    def test_rejects_boolean_integer(self):
        self.config["cpus"] = True
        with self.assertRaisesRegex(ConfigError, "cpus must be an integer"):
            validate_config(self.config)


class GuiApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.token = "test-token"
        cls.server = WinebundleServer(("127.0.0.1", 0), RequestHandler, cls.token)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.port = cls.server.server_port

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def request(self, method, path, body=None, token=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {}
        encoded = None
        if body is not None:
            encoded = json.dumps(body)
            headers["Content-Type"] = "application/json"
        if token is not None:
            headers["X-Winebundle-Token"] = token
        connection.request(method, path, body=encoded, headers=headers)
        response = connection.getresponse()
        content = response.read()
        connection.close()
        return response.status, json.loads(content) if content else None

    def valid_config(self):
        return {
            "version": 1,
            "installer": "/does/not/exist.exe",
            "entrypoint": r"C:\test.exe",
            "output": "/tmp/winebundle-output",
        }

    def test_session_and_validation(self):
        status, session = self.request("GET", "/api/session")
        self.assertEqual(status, 200)
        self.assertEqual(session["token"], self.token)

        status, result = self.request(
            "POST",
            "/api/config/validate",
            {"config": self.valid_config()},
            self.token,
        )
        self.assertEqual(status, 200)
        self.assertTrue(result["valid"])

    def test_rejects_missing_token(self):
        status, result = self.request(
            "POST", "/api/config/validate", {"config": self.valid_config()}
        )
        self.assertEqual(status, 403)
        self.assertIn("token", result["error"])

    def test_picker_and_runner_routes_require_token(self):
        status, session = self.request("GET", "/api/session")
        self.assertIn("runner", session["local"])
        self.assertNotIn("local", session["defaults"])
        self.assertTrue(any(place["label"] == "Computer" for place in session["local"]["places"]))
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "setup.exe").write_text("x")
            Path(directory, "notes.txt").write_text("x")
            query = f"/api/browse?path={directory}&kind=installer"
            self.assertEqual(self.request("GET", query)[0], 403)
            status, result = self.request("GET", query, token=self.token)
            self.assertEqual(status, 200)
            self.assertEqual([entry["name"] for entry in result["entries"]], ["setup.exe"])
            status, result = self.request("GET", f"/api/browse?path={directory}/missing", token=self.token)
            self.assertEqual(status, 400)
            self.assertIn("not found", result["error"])
            self.assertEqual(self.request("GET", f"/api/runner?path={directory}")[0], 403)
            status, result = self.request("GET", f"/api/runner?path={directory}/setup.exe", token=self.token)
            self.assertEqual(status, 200)
            self.assertIn("No executable", result["error"])

    def test_system_check_route_requires_token(self):
        self.assertEqual(self.request("GET", "/api/system")[0], 403)
        status, result = self.request("GET", "/api/system", token=self.token)
        self.assertEqual(status, 200)
        self.assertEqual(result["checks"][0]["id"], "wine")
        self.assertIn("family", result["distro"])
        self.assertIsInstance(result["ready"], bool)

    def test_library_is_home_and_builder_remains_available(self):
        for path, expected in (("/", "Your games"), ("/local", "Your games"),
                               ("/builder", "Bundle inputs")):
            with self.subTest(path=path):
                connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
                connection.request("GET", path)
                response = connection.getresponse()
                content = response.read().decode()
                connection.close()
                self.assertEqual(response.status, 200)
                self.assertIn(expected, content)

    def test_local_routes_and_authorization(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = root / "wine"
            runner.write_text("#!/bin/sh\nexit 0\n")
            runner.chmod(0o755)
            server = root / "wineserver"
            server.write_text("#!/bin/sh\nexit 0\n")
            server.chmod(0o755)
            game = root / "game.exe"
            game.write_text("game")
            with patch.object(self.server, "local", LocalSetup(root / "setups")):
                self.assertEqual(self.request("GET", "/api/local")[0], 403)
                values = dict(name="API test", runner=str(runner),
                              mode="existing", source=str(game))
                self.assertEqual(self.request("POST", "/api/local", values)[0], 403)
                status, result = self.request("POST", "/api/local", values, self.token)
                self.assertEqual(status, 201)
                game_id = result["id"]
                status, setups = self.request("GET", "/api/local", token=self.token)
                self.assertEqual(status, 200)
                self.assertEqual(setups[0]["id"], game_id)
                status, state = self.request("GET", f"/api/local/{game_id}", token=self.token)
                self.assertEqual(state["config"]["name"], "API test")
                self.assertEqual(self.request("POST", f"/api/local/{game_id}/save",
                                             {"accepted": True}, self.token)[0], 400)
                self.assertEqual(self.request("POST", f"/api/local/{game_id}/play", {})[0], 403)
                self.assertEqual(self.request("POST", f"/api/local/{game_id}/play",
                                             {}, self.token)[0], 400)
                with patch.object(self.server.local, "start") as start:
                    self.assertEqual(self.request("POST", f"/api/local/{game_id}/play",
                                                 {}, self.token)[0], 202)
                    start.assert_called_once_with(game_id, "play", {})
                self.assertEqual(self.request("POST", "/api/local", [], self.token)[0], 400)
                self.assertEqual(self.request("GET", "/api/local/not-an-id", token=self.token)[0], 400)

    def test_failed_job_reports_log(self):
        status, result = self.request(
            "POST", "/api/jobs", {"config": self.valid_config()}, self.token
        )
        self.assertEqual(status, 202)
        job_id = result["jobId"]

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            status, job = self.request("GET", f"/api/jobs/{job_id}", token=self.token)
            self.assertEqual(status, 200)
            if job["status"] == "failed":
                break
            time.sleep(0.05)
        else:
            self.fail("job did not finish")

        self.assertNotEqual(job["exitCode"], 0)
        self.assertTrue(job["log"].strip())


if __name__ == "__main__":
    unittest.main()
