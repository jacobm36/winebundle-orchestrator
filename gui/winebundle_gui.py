#!/usr/bin/env python3

import argparse
import ipaddress
import json
import os
import secrets
import subprocess
import sys
import tempfile
import threading
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parents[1]
STATIC = Path(__file__).resolve().parent / "static"
sys.path.insert(0, str(ROOT / "lib"))

from winebundle_config import (  # noqa: E402
    ConfigError,
    normalized_config,
    validate_config,
    write_config,
)
from local_setup import (  # noqa: E402
    LocalSetup, browse, check_runner, check_source, default_runner, places)
import system_check  # noqa: E402


MAX_REQUEST_SIZE = 1024 * 1024
MAX_LOG_SIZE = 2 * 1024 * 1024
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
}


class JobStore:
    def __init__(self):
        self.jobs = {}
        self.lock = threading.Lock()

    def create(self, config):
        job_id = uuid.uuid4().hex
        config = normalized_config(config, ROOT)
        with self.lock:
            self.jobs[job_id] = {
                "id": job_id,
                "status": "queued",
                "exitCode": None,
                "log": "",
            }
        thread = threading.Thread(target=self._run, args=(job_id, config), daemon=True)
        thread.start()
        return job_id

    def get(self, job_id):
        with self.lock:
            job = self.jobs.get(job_id)
            return dict(job) if job else None

    def _update(self, job_id, **values):
        with self.lock:
            self.jobs[job_id].update(values)

    def _append_log(self, job_id, text):
        with self.lock:
            log = self.jobs[job_id]["log"] + text
            if len(log) > MAX_LOG_SIZE:
                log = "[Earlier output omitted]\n" + log[-MAX_LOG_SIZE:]
            self.jobs[job_id]["log"] = log

    def _run(self, job_id, config):
        config_path = None
        try:
            with tempfile.NamedTemporaryFile(
                prefix="winebundle-gui-", suffix=".json", delete=False
            ) as config_file:
                config_path = Path(config_file.name)
            write_config(config_path, config)
            self._update(job_id, status="running")
            process = subprocess.Popen(
                [
                    "bash",
                    str(ROOT / "bin" / "winebundle-vm"),
                    "run",
                    "--config",
                    str(config_path),
                ],
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            assert process.stdout is not None
            for line in process.stdout:
                self._append_log(job_id, line)
            exit_code = process.wait()
            self._update(
                job_id,
                status="succeeded" if exit_code == 0 else "failed",
                exitCode=exit_code,
            )
        except (OSError, ConfigError) as exc:
            self._append_log(job_id, f"winebundle GUI: {exc}\n")
            self._update(job_id, status="failed", exitCode=1)
        finally:
            if config_path is not None:
                try:
                    config_path.unlink()
                except FileNotFoundError:
                    pass


class WinebundleServer(ThreadingHTTPServer):
    def __init__(self, address, handler, token, builder=True):
        super().__init__(address, handler)
        self.token = token
        self.builder = builder
        # The page opens with this one-time code in its URL fragment and trades it for the
        # token, so other local programs can't simply ask the server for the token.
        self.launch_code = secrets.token_urlsafe(32)
        self.launch_lock = threading.Lock()
        self.jobs = JobStore()
        self.local = LocalSetup()
        # Set by the app window when it needs an in-page Quit button (no title bar).
        self.on_quit = None

    def claim_launch(self, code):
        with self.launch_lock:
            if not self.launch_code or not secrets.compare_digest(code, self.launch_code):
                return False
            self.launch_code = ""
            return True

    def launch_url(self, base):
        return f"{base}#launch={self.launch_code}"


class RequestHandler(BaseHTTPRequestHandler):
    server_version = "winebundle-gui"

    def log_message(self, format_string, *args):
        print(f"winebundle GUI: {self.address_string()} - {format_string % args}")

    def _is_loopback(self):
        try:
            if not ipaddress.ip_address(self.client_address[0]).is_loopback:
                return False
        except ValueError:
            return False
        # Rejecting other Host names blocks DNS-rebinding pages from reading the token.
        port = self.server.server_port
        return self.headers.get("Host", "") in (
            f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}")

    def _security_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'self'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'",
        )

    def _send_bytes(self, status, content_type, body):
        self.send_response(status)
        self._security_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status, body):
        encoded = json.dumps(body).encode("utf-8")
        self._send_bytes(status, "application/json; charset=utf-8", encoded)

    def _authorized(self):
        return secrets.compare_digest(
            self.headers.get("X-Winebundle-Token", ""), self.server.token
        )

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ConfigError("invalid Content-Length")
        if length < 1 or length > MAX_REQUEST_SIZE:
            raise ConfigError("request body must be between 1 byte and 1 MiB")
        try:
            return json.loads(self.rfile.read(length))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ConfigError(f"invalid JSON request: {exc}")

    def do_GET(self):
        if not self._is_loopback():
            self._send_json(403, {"error": "loopback access only"})
            return

        path = urlparse(self.path).path
        if path == "/api/session":
            if not (self._authorized() or self.server.claim_launch(
                    self.headers.get("X-Winebundle-Launch", ""))):
                self._send_json(403, {"error": "This Winebundle link has already been used. "
                                      "Use the window that's already open, or restart Winebundle."})
                return
            self._send_json(
                200,
                {
                    "token": self.server.token,
                    "builder": self.server.builder,
                    "quit": self.server.on_quit is not None,
                    "defaults": {
                        "version": 1,
                        "installArgs": [],
                        "smokeArgs": [],
                        "winetricks": [],
                        "arch": "win64",
                        "timeout": 180,
                        "image": "24.04",
                        "cpus": 2,
                        "memory": "4G",
                        "disk": "20G",
                        "keepHostDrive": False,
                        "skipProvision": False,
                        "keepVm": False,
                    },
                    "local": {"runner": default_runner(), "places": places()},
                },
            )
            return
        if path == "/api/preferences":
            if not self._authorized():
                self._send_json(403, {"error": "invalid session token"})
            else:
                self._send_json(200, self.server.local.preferences())
            return
        if path == "/api/system":
            if not self._authorized():
                self._send_json(403, {"error": "invalid session token"})
            else:
                self._send_json(200, system_check.run(self.server.local.root))
            return
        if path in ("/api/browse", "/api/runner", "/api/installer-check"):
            if not self._authorized():
                self._send_json(403, {"error": "invalid session token"})
                return
            query = parse_qs(urlparse(self.path).query)
            value = query.get("path", [""])[0]
            try:
                if path == "/api/runner":
                    self._send_json(200, check_runner(value))
                elif path == "/api/installer-check":
                    self._send_json(200, check_source(value, query.get("mode", ["installer"])[0]))
                else:
                    self._send_json(200, browse(value, query.get("kind", ["installer"])[0],
                                                query.get("hidden", ["0"])[0] == "1"))
            except (ConfigError, OSError) as exc:
                self._send_json(400, {"error": str(exc)})
            return
        if path.startswith("/api/jobs/") and self.server.builder:
            if not self._authorized():
                self._send_json(403, {"error": "invalid session token"})
                return

            job = self.server.jobs.get(path.removeprefix("/api/jobs/"))
            if job is None:
                self._send_json(404, {"error": "job not found"})
            else:
                self._send_json(200, job)
            return

        if path == "/api/local" or path.startswith("/api/local/"):
            if not self._authorized():
                self._send_json(403, {"error": "invalid session token"})
                return
            try:
                result = (self.server.local.list() if path == "/api/local"
                          else self.server.local.get(path.removeprefix("/api/local/")))
                self._send_json(200, result)
            except (ConfigError, OSError) as exc:
                self._send_json(400, {"error": str(exc)})
            return

        pages = {
            "/": STATIC / "local.html",
            "/app.css": STATIC / "app.css",
            "/local": STATIC / "local.html",
            "/local.js": STATIC / "local.js",
        }
        if self.server.builder:
            pages.update({"/builder": STATIC / "index.html", "/app.js": STATIC / "app.js"})
        static_path = pages.get(path)
        if static_path is None:
            self._send_json(404, {"error": "not found"})
            return
        try:
            body = static_path.read_bytes()
        except OSError as exc:
            self._send_json(500, {"error": f"cannot read GUI asset: {exc}"})
            return
        self._send_bytes(200, CONTENT_TYPES[static_path.suffix], body)

    def do_POST(self):
        if not self._is_loopback():
            self._send_json(403, {"error": "loopback access only"})
            return
        if not self._authorized():
            self._send_json(403, {"error": "invalid session token"})
            return

        path = urlparse(self.path).path
        try:
            request = self._read_json()
            if not isinstance(request, dict):
                raise ConfigError("request must be an object")
            if path == "/api/local":
                self._send_json(201, {"id": self.server.local.create(request)})
                return
            if path == "/api/preferences":
                self._send_json(200, self.server.local.set_preferences(request))
                return
            if path == "/api/quit" and self.server.on_quit:
                self.server.on_quit()
                self._send_json(200, {"quitting": True})
                return
            if path.startswith("/api/local/"):
                parts = path.removeprefix("/api/local/").split("/")
                if len(parts) != 2:
                    raise ConfigError("invalid local operation URL")
                game_id, action = parts
                if action == "save":
                    self._send_json(200, self.server.local.save(
                        game_id, request.get("accepted"),
                        request.get("menu", True), request.get("desktop", False)))
                elif action == "shortcuts":
                    self._send_json(200, self.server.local.shortcuts(
                        game_id, request.get("menu"), request.get("desktop")))
                elif action == "graphics":
                    self._send_json(200, self.server.local.set_graphics(
                        game_id, request.get("discrete")))
                elif action == "remove":
                    self._send_json(200, self.server.local.remove(game_id))
                elif action == "stop":
                    self.server.local.stop(game_id)
                    self._send_json(200, {"stopped": True})
                else:
                    self.server.local.start(game_id, action, request)
                    self._send_json(202, {"id": game_id})
                return
            if not self.server.builder:
                self._send_json(404, {"error": "not found"})
                return
            config = validate_config(request.get("config"))
            if path == "/api/config/validate":
                self._send_json(200, {"valid": True})
            elif path == "/api/jobs":
                job_id = self.server.jobs.create(config)
                self._send_json(202, {"jobId": job_id})
            else:
                self._send_json(404, {"error": "not found"})
        except (AttributeError, ConfigError, OSError, subprocess.SubprocessError) as exc:
            self._send_json(400, {"error": str(exc)})


def main():
    parser = argparse.ArgumentParser(description="Run the local winebundle web GUI")
    parser.add_argument("--host", default="127.0.0.1", choices=("127.0.0.1", "::1"))
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")

    token = secrets.token_urlsafe(32)
    server = WinebundleServer((args.host, args.port), RequestHandler, token)
    host = f"[{args.host}]" if ":" in args.host else args.host
    url = server.launch_url(f"http://{host}:{server.server_port}/")
    print(f"winebundle GUI: listening at {url} (this link works once)")
    print("winebundle GUI: press Ctrl+C to stop", flush=True)
    threading.Thread(target=server.local.refresh_launchers, daemon=True).start()
    if not args.no_browser:
        threading.Timer(0.25, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nwinebundle GUI: stopping")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
