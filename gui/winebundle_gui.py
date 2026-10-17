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
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
STATIC = Path(__file__).resolve().parent / "static"
sys.path.insert(0, str(ROOT / "lib"))

from winebundle_config import (  # noqa: E402
    ConfigError,
    normalized_config,
    validate_config,
    write_config,
)


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
    def __init__(self, address, handler, token):
        super().__init__(address, handler)
        self.token = token
        self.jobs = JobStore()


class RequestHandler(BaseHTTPRequestHandler):
    server_version = "winebundle-gui"

    def log_message(self, format_string, *args):
        print(f"winebundle GUI: {self.address_string()} - {format_string % args}")

    def _is_loopback(self):
        try:
            return ipaddress.ip_address(self.client_address[0]).is_loopback
        except ValueError:
            return False

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
            self._send_json(
                200,
                {
                    "token": self.server.token,
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
                },
            )
            return
        if path.startswith("/api/jobs/"):
            if not self._authorized():
                self._send_json(403, {"error": "invalid session token"})
                return
            job = self.server.jobs.get(path.removeprefix("/api/jobs/"))
            if job is None:
                self._send_json(404, {"error": "job not found"})
            else:
                self._send_json(200, job)
            return

        static_path = {
            "/": STATIC / "index.html",
            "/app.css": STATIC / "app.css",
            "/app.js": STATIC / "app.js",
        }.get(path)
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
            config = validate_config(request.get("config"))
            if path == "/api/config/validate":
                self._send_json(200, {"valid": True})
            elif path == "/api/jobs":
                job_id = self.server.jobs.create(config)
                self._send_json(202, {"jobId": job_id})
            else:
                self._send_json(404, {"error": "not found"})
        except (AttributeError, ConfigError) as exc:
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
    url = f"http://{host}:{server.server_port}/"
    print(f"winebundle GUI: listening at {url}")
    print("winebundle GUI: press Ctrl+C to stop")
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
